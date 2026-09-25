"""One synchronous session owner; call long commands from one worker thread.

``cancel`` is the sole out-of-band command. Events include session and operation
IDs so delayed UI delivery cannot overwrite a newer session.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading
import uuid

from engine import Cancelled, run_benchmark, selected_resident
from model_studio.backends.llama_cpp import LlamaCppBackend
from model_studio.backends.ollama import OllamaBackend
from model_studio.backends.images import image_mime
from model_studio.backends.process import ManagedRuntime
from model_studio.configuration import LaunchConfig
from model_studio.domain import SessionBusy, SessionUnavailable, StreamChunk


class SessionController:
    POLLABLE_STATUSES = ("ready", "disconnected", "context_changed", "model_unloaded")

    def __init__(self, logs_dir, emit):
        self.logs_dir = Path(logs_dir)
        self.emit = emit
        self._lock = threading.RLock()
        self._status = "stopped"
        self._busy = "idle"
        self._operation = None
        self._stop = None
        self._generation = 0
        self._session_id = None
        self._config: LaunchConfig | None = None
        self._client = None
        self._model_id = None
        self._owned: ManagedRuntime | None = None
        self._error = None
        self._research_thread = None
        self._research_step_depth = 0

    @property
    def snapshot(self) -> dict:
        with self._lock:
            config = self._config
            client = self._client
            info = client.model_info if client is not None else {}
            return {"status": self._status, "busy": self._busy,
                    "model": config.model if config else None,
                    "context": config.context if config else None,
                    "effective_context": info.get("context_limit"),
                    "context_source": info.get("context_source"),
                    "vision_available": getattr(client, "vision_available", None),
                    "backend": config.backend if config else None,
                    "host": client.host if client else (config.host if config else None),
                    "owned": bool(self._owned and self._owned.running),
                    "session_id": self._session_id,
                    "config": config.to_dict() if config else None,
                    "operation_id": self._operation, "model_id": self._model_id,
                    "error": self._error}

    @property
    def client(self):
        """Read-only access for the serialized research worker; do not mutate it."""
        with self._lock:
            return self._client

    @property
    def model_id(self):
        with self._lock:
            return self._model_id

    @property
    def cancel_event(self):
        with self._lock:
            return self._stop

    def _nested_research(self) -> bool:
        return self._busy == "research" and self._research_thread == threading.get_ident()

    def _publish(self, event: str, payload: dict) -> None:
        with self._lock:
            envelope = {"session_id": self._session_id, "operation_id": self._operation,
                        **payload}
        self.emit(event, envelope)

    def _state(self) -> None:
        self._publish("session", self.snapshot)

    def _begin(self, busy: str = "idle", require_ready: bool = False):
        with self._lock:
            if self._operation is not None and not self._nested_research():
                raise SessionBusy(f"Session operation {self._operation} is still running.")
            if require_ready and self._status != "ready":
                raise SessionUnavailable(f"Session is {self._status}; start a model first.")
            if self._nested_research():
                self._research_step_depth += 1
                return self._operation, self._stop
            self._operation = uuid.uuid4().hex
            self._stop = threading.Event()
            self._busy = busy
            return self._operation, self._stop

    def _finish(self, operation_id):
        with self._lock:
            if self._nested_research():
                self._research_step_depth -= 1
                return
            if self._operation == operation_id:
                self._operation = None
                self._stop = None
                self._busy = "idle"
        self._state()

    def cancel(self) -> bool:
        """Signal the current request without waiting for its worker thread."""
        with self._lock:
            stop, client = self._stop, self._client
        if stop is None:
            return False
        stop.set()
        if client is not None:
            client.cancel()
        return True

    def start(self, config: LaunchConfig) -> dict:
        if not isinstance(config, LaunchConfig):
            config = LaunchConfig.from_dict(config)
        with self._lock:
            if self._operation is not None and not self._nested_research():
                raise SessionBusy(f"Session operation {self._operation} is still running.")
            if self._status == "ready" and self._config == config:
                return self.snapshot
        operation, stop = self._begin()
        try:
            with self._lock:
                previous_status = self._status
                self._status = "starting"
                self._error = None
            self._state()
            if stop.is_set():
                raise Cancelled()
            # A changed launch is an explicit replacement. Old owned server is
            # released before starting the next one; a failed launch stays failed.
            self._release(stop_old=previous_status not in ("disconnected", "failed", "context_changed", "model_unloaded"))
            if stop.is_set():
                raise Cancelled()
            owned = None
            if config.backend == "ollama":
                client = OllamaBackend(config.host, config.context)
                client.client.reasoning = config.reasoning
                client.prepare(config.model)
                client.preload(config.model, stop)
                model_id = config.model
            elif config.managed:
                owned = ManagedRuntime(self.logs_dir)
                client, model_id = owned.start(config, stop, self._publish)
            else:
                client = LlamaCppBackend(config.host, config.context)
                model_id = config.model
                client.prepare(model_id)
                try:
                    props = client.request("/props", timeout=3)
                    modalities = props.get("modalities") or {}
                    vision = modalities.get("vision") if isinstance(modalities, dict) else None
                    client.vision_available = vision if type(vision) is bool else None
                except Exception:
                    client.vision_available = None
                actual = client.model_info.get("context_limit")
                if actual is not None and actual != config.context:
                    raise RuntimeError(f"External server context {actual} differs from requested {config.context}.")
            if stop.is_set():
                if owned:
                    owned.stop()
                raise Cancelled()
            with self._lock:
                self._generation += 1
                self._session_id = uuid.uuid4().hex
                self._client, self._model_id = client, model_id
                self._config, self._owned = config, owned
                self._status = "ready"
            self._state()
        except BaseException as exc:
            with self._lock:
                self._status = ("ready" if self._client is not None else "stopped") \
                    if isinstance(exc, Cancelled) else "failed"
                self._error = "Cancelled" if isinstance(exc, Cancelled) else str(exc)
            self._state()
            if isinstance(exc, Cancelled):
                pass
            else:
                raise
        finally:
            self._finish(operation)
        return self.snapshot

    def _release(self, stop_old: bool = True):
        with self._lock:
            owned, client, model_id, config = self._owned, self._client, self._model_id, self._config
        if owned:
            owned.stop()
        elif client is not None and config and config.backend == "ollama" and stop_old:
            client.unload(model_id)
        # An external llama-server is only disconnected; Ollama unload affects
        # the selected model through its API and never stops the service.
        with self._lock:
            self._owned = self._client = self._model_id = self._config = None
            self._session_id = None

    def _check_connection_after_error(self, client, session_id):
        """Distinguish a request error from a lost external connection."""
        with self._lock:
            owned = self._owned
            if client is not self._client or session_id != self._session_id:
                return False
        if owned and not owned.running:
            next_status, reason = "failed", "Owned llama-server exited."
        else:
            try:
                client.request("/api/version" if client.backend == "ollama" else "/health", timeout=2)
                return True
            except Exception as exc:
                next_status = "failed" if owned else "disconnected"
                reason = str(exc)
        with self._lock:
            if client is self._client and session_id == self._session_id:
                self._status, self._error = next_status, reason
        self._state()
        return False

    def unload(self) -> dict:
        with self._lock:
            if self._operation is not None and not self._nested_research():
                raise SessionBusy(f"Session operation {self._operation} is still running.")
            if self._status == "stopped":
                return self.snapshot
        operation, _ = self._begin()
        try:
            with self._lock:
                previous_status = self._status
                self._status = "stopping"
            self._state()
            try:
                self._release(stop_old=previous_status not in ("disconnected", "context_changed", "model_unloaded"))
            except Exception as exc:
                with self._lock:
                    self._status = "failed"
                    self._error = str(exc)
                self._state()
                raise
            with self._lock:
                self._status = "stopped"
                self._error = None
            self._state()
        finally:
            self._finish(operation)
        return self.snapshot

    @contextmanager
    def research_operation(self):
        """Exclusive lease across multiple launches/tests, including from stopped."""
        with self._lock:
            if self._nested_research():
                raise SessionBusy("Research lease is already active on this worker.")
        operation, _ = self._begin("research")
        with self._lock:
            self._research_thread = threading.get_ident()
        try:
            self._state()
            yield self
        finally:
            with self._lock:
                self._research_thread = None
            self._finish(operation)

    def begin_restoration(self) -> threading.Event:
        """Give restoration a new cancellation token after a stopped research step."""
        with self._lock:
            if not self._nested_research() or self._research_step_depth:
                raise SessionBusy("Restoration can start only between steps of the research lease.")
            self._stop = threading.Event()
            return self._stop

    def refresh_status(self) -> dict:
        """Read-only health checks; recover only after verifying the active context."""
        with self._lock:
            if (self._operation is not None or self._status not in self.POLLABLE_STATUSES
                    or self._client is None):
                return self.snapshot
        operation, _ = self._begin()
        try:
            with self._lock:
                client, session_id, model_id, config = self._client, self._session_id, self._model_id, self._config
            if self._check_connection_after_error(client, session_id):
                status, error = "ready", None
                try:
                    if client.backend == "ollama":
                        resident = selected_resident(client.loaded_models(), model_id)
                        actual = resident.get("context_length") if resident else None
                        client.model_info = dict(client.model_info, context_limit=actual)
                        if resident is None:
                            status, error = "model_unloaded", "Ollama доступна, но выбранная модель выгружена."
                        elif actual != config.context:
                            status = "context_changed"
                            error = f"Ollama: фактический контекст {actual}, запрошен {config.context}. Запустите модель заново, чтобы применить настройки."
                    elif self.snapshot["status"] != "ready":
                        client.prepare(model_id)
                        actual = client.model_info.get("context_limit")
                        if actual != config.context:
                            status, error = "context_changed", f"Контекст сервера {actual}, запрошен {config.context}."
                except Exception as exc:
                    status, error = "disconnected", str(exc)
                with self._lock:
                    if client is self._client and session_id == self._session_id:
                        self._status, self._error = status, error
        finally:
            self._finish(operation)
        return self.snapshot

    def chat(self, messages: list[dict], max_tokens: int = 2048,
             temperature: float = .7, on_chunk=None) -> dict:
        if not isinstance(messages, list) or not messages:
            raise ValueError("Chat requires at least one message.")
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("max_tokens must be positive.")
        if not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2.")
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in ("system", "user", "assistant") \
                    or not isinstance(message.get("content"), str):
                raise ValueError("Chat messages need a role and string content.")
            images = message.get("images", [])
            if not isinstance(images, list) or any(not isinstance(item, str) for item in images):
                raise ValueError("Chat images must be a list of base64 strings.")
            if images and message["role"] != "user":
                raise ValueError("Chat images are supported only in user messages.")
            for encoded in images:
                image_mime(encoded)
        if any(message.get("images") for message in messages) and self.snapshot["vision_available"] is not True:
            raise ValueError("The active runtime has not confirmed image input support.")
        operation, stop = self._begin("chat", require_ready=True)
        with self._lock:
            client, model_id, session_id = self._client, self._model_id, self._session_id
        text_parts, reasoning_parts = [], []
        metrics = {}
        status = "complete"
        try:
            self._state()
            stream = client.chat(model_id, messages, max_tokens, float(temperature), stop)
            try:
                for item in stream:
                    if stop.is_set():
                        raise Cancelled()
                    if isinstance(item, StreamChunk):
                        (text_parts if item.kind == "text" else reasoning_parts).append(item.text)
                        self._publish(item.kind, {"text": item.text})
                        if on_chunk is not None:
                            on_chunk(item)
                    elif isinstance(item, dict):
                        metrics.update(item)
                if stop.is_set():
                    raise Cancelled()
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()
        except Cancelled:
            status = "cancelled"
        except Exception as exc:
            status = "cancelled" if stop.is_set() else "error"
            if status == "error":
                metrics["error"] = str(exc)
                self._publish("error", {"message": str(exc)})
                self._check_connection_after_error(client, session_id)
        finally:
            result = {"status": status, "text": "".join(text_parts),
                      "reasoning": "".join(reasoning_parts), "metrics": metrics,
                      "session_id": session_id, "operation_id": operation,
                      "config": self._config.to_dict() if self._config else None}
            try:
                self._publish("chat_finished", result)
            finally:
                self._finish(operation)
        return result

    def benchmark(self, runs: int = 3, tokens: int = 512) -> dict:
        if type(runs) is not int or not 1 <= runs <= 100:
            raise ValueError("runs must be between 1 and 100.")
        if type(tokens) is not int or not 1 <= tokens <= 32768:
            raise ValueError("tokens must be between 1 and 32768.")
        operation, stop = self._begin("benchmark", require_ready=True)
        with self._lock:
            client, model_id, config, session_id = self._client, self._model_id, self._config, self._session_id
        result = None
        try:
            self._state()
            def relay(event, payload):
                if event == "status":
                    self._publish("progress", {"message": str(payload)})
                elif event == "finished":
                    pass  # publish once after adding session provenance below
                else:
                    self._publish(event, payload if isinstance(payload, dict) else {"message": str(payload)})

            result = run_benchmark(client, model_id, relay, stop, None, runs=runs, tokens=tokens)
            result.update(session_id=session_id, operation_id=operation,
                          config=config.to_dict(), model_id=config.model_id or None,
                          runtime_model_id=model_id, method="legacy-short-v2")
            if result["status"] == "error":
                self._check_connection_after_error(client, session_id)
        finally:
            try:
                if result is not None:
                    self._publish("benchmark_finished", result)
            finally:
                self._finish(operation)
        return result
