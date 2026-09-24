"""Persist a conversation while the shared session streams its response.

No Qt objects live here. The caller supplies an event sink, and the session
continues publishing its normal text/reasoning and state events separately.
"""
from __future__ import annotations

import time

from model_studio.attachments import AttachmentError, MAX_IMAGES, load_image, reference


class ChatPersistenceError(RuntimeError):
    def __init__(self, message: str, result: dict, saved: bool = False):
        super().__init__(message)
        self.result = result
        self.saved = saved


class ChatContextOverflow(ValueError):
    pass


class ChatService:
    def __init__(self, store, session, emit, clock=None, attachments_root=None):
        self.store = store
        self.session = session
        self.emit = emit
        self._clock = clock or time.monotonic
        self.attachments_root = attachments_root or store.path.parent

    @staticmethod
    def _estimate(messages: list[dict]) -> int:
        # UTF-8 byte count is deliberately conservative for ordinary text.
        # Fixed overhead covers chat-template boundaries; this is still an
        # estimate, so the runtime must reject overflow rather than truncate.
        return 256 + sum(len(message["content"].encode("utf-8")) + 32
                         + 4096 * len(message.get("images") or []) for message in messages)

    def send(self, conversation_id: str | None, text: str, max_tokens: int = 2048,
             attachments: list[dict] | None = None) -> dict:
        if not isinstance(text, str):
            raise ValueError("Message text must be a string.")
        if not text.strip() and not attachments:
            raise ValueError("Message text or an image is required.")
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("max_tokens must be positive.")
        if attachments is not None and (not isinstance(attachments, list)
                                        or len(attachments) > MAX_IMAGES):
            raise AttachmentError(f"Можно добавить не больше {MAX_IMAGES} изображений.")
        text = text.strip()
        snapshot = self.session.snapshot
        if snapshot.get("status") != "ready":
            raise RuntimeError("Start a model before sending a message.")
        config = snapshot.get("config") or {}
        if conversation_id:
            history = self.store.messages(conversation_id)
        else:
            history = []
        request = []
        for message in history:
            if (message.get("role") not in ("system", "user", "assistant")
                    or message.get("status") != "complete"
                    or not isinstance(message.get("text"), str)):
                continue
            item = {"role": message["role"], "content": message["text"]}
            prior = (message.get("metadata") or {}).get("attachments") or []
            if prior:
                if not isinstance(prior, list) or len(prior) > MAX_IMAGES:
                    raise AttachmentError("История содержит повреждённые ссылки на изображения.")
                item["images"] = [load_image(image, self.attachments_root) for image in prior]
            request.append(item)
        saved_attachments = [reference(image) for image in (attachments or [])]
        current = {"role": "user", "content": text}
        if saved_attachments:
            current["images"] = [load_image(image, self.attachments_root)
                                 for image in saved_attachments]
        request.append(current)
        if any(message.get("images") for message in request) and snapshot.get("vision_available") is not True:
            raise AttachmentError("Активная модель не подтвердила поддержку изображений.")
        estimate = self._estimate(request)
        requested_context = snapshot.get("context")
        effective_context = snapshot.get("effective_context")
        limits = [value for value in (requested_context, effective_context)
                  if isinstance(value, int) and value > 0]
        if not limits:
            raise ChatContextOverflow("Context limit is unknown; select a verified session.")
        context = min(limits)
        if estimate + max_tokens > context:
            raise ChatContextOverflow(
                f"History may need about {estimate} input tokens plus {max_tokens} output tokens; "
                f"the session context is {context}. Start a new conversation or shorten the input.")
        try:
            if not conversation_id:
                conversation_id = self.store.create_conversation(text[:60] or "Изображение")["id"]
            self.store.save_message(conversation_id, "user", text,
                                    metadata={"attachments": saved_attachments})
        except Exception as exc:
            unsaved = {"stage": "user", "conversation_id": conversation_id,
                       "user_text": text, "attachments": saved_attachments,
                       "text": "", "reasoning": "",
                       "status": "error", "save_error": str(exc), "user_saved": False}
            self.emit("chat_unsaved", unsaved)
            raise ChatPersistenceError(f"User message was not saved: {exc}", unsaved) from exc
        metadata = {"config": config, "session_id": snapshot.get("session_id"),
                    "prompt_tokens_estimate": estimate,
                    "prompt_estimate_basis": "UTF-8 bytes + 32/message + 256 template reserve + 4096/image",
                    "requested_max_tokens": max_tokens,
                    "effective_context": effective_context}
        try:
            placeholder = self.store.save_message(conversation_id, "assistant", "", "streaming",
                                                  metadata=metadata)
        except Exception as exc:
            partial = {"conversation_id": conversation_id, "text": "", "reasoning": "",
                       "status": "error", "save_error": str(exc), "user_saved": True}
            self.emit("chat_unsaved", partial)
            raise ChatPersistenceError(f"Could not create assistant message: {exc}", partial) from exc
        message_id = placeholder["id"]
        self.emit("chat_started", {"conversation_id": conversation_id,
                                   "messages": self.store.messages(conversation_id)})
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        last_saved = self._clock()
        checkpoint_error = None

        def on_chunk(chunk):
            nonlocal last_saved, checkpoint_error
            (text_parts if chunk.kind == "text" else reasoning_parts).append(chunk.text)
            now = self._clock()
            if now - last_saved < 1:
                return
            try:
                self.store.save_message(conversation_id, "assistant", "".join(text_parts),
                                        "streaming", "".join(reasoning_parts), metadata, message_id)
                last_saved = now
            except Exception as exc:
                checkpoint_error = str(exc)
                self.session.cancel()
                raise ChatPersistenceError(f"Chat checkpoint could not be saved: {exc}",
                                           {"conversation_id": conversation_id,
                                            "text": "".join(text_parts),
                                            "reasoning": "".join(reasoning_parts),
                                            "status": "cancelled", "save_error": str(exc)}) from exc

        try:
            result = self.session.chat(request, max_tokens=max_tokens, on_chunk=on_chunk)
        except Exception as exc:
            result = {"status": "error", "text": "".join(text_parts),
                      "reasoning": "".join(reasoning_parts), "metrics": {"error": str(exc)}}
            core_error = exc
        else:
            core_error = None
        result["conversation_id"] = conversation_id
        result["message_id"] = message_id
        if checkpoint_error:
            result["persistence_error"] = checkpoint_error
        final_metadata = {**metadata, **(result.get("metrics") or {})}
        if checkpoint_error:
            final_metadata["checkpoint_error"] = checkpoint_error
        try:
            self.store.save_message(conversation_id, "assistant", result["text"],
                                    result["status"], result["reasoning"], final_metadata, message_id)
        except Exception as exc:
            unsaved = {**result, "save_error": str(exc), "user_saved": True}
            self.emit("chat_unsaved", unsaved)
            raise ChatPersistenceError(f"Assistant response was not saved: {exc}", unsaved) from exc
        if checkpoint_error:
            self.emit("chat_save_error", {"conversation_id": conversation_id,
                                          "message_id": message_id, "message": checkpoint_error,
                                          "saved": True, "result": result})
        if core_error:
            raise core_error
        return {"conversation_id": conversation_id,
                "messages": self.store.messages(conversation_id), "result": result}
