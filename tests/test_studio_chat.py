"""Durable chat flow with real SQLite and a controlled model stream."""
from __future__ import annotations

from pathlib import Path
import base64
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_studio.chat import ChatContextOverflow, ChatPersistenceError, ChatService
from model_studio.attachments import AttachmentError, import_image
from model_studio.domain import StreamChunk
from model_studio.storage.store import Store
from tests.test_studio_attachments import png_pixel


class FakeSession:
    def __init__(self):
        self.snapshot = {"status": "ready", "context": 4096, "effective_context": 4096,
                         "session_id": "session-1", "config": {"model": "demo"}}
        self.requests = []
        self.budgets = []
        self.cancelled = False
        self.mode = "complete"

    def cancel(self):
        self.cancelled = True
        return True

    def chat(self, messages, max_tokens=2048, on_chunk=None):
        self.requests.append(messages)
        self.budgets.append(max_tokens)
        chunks = [StreamChunk("text", "part")]
        if self.mode == "complete":
            chunks += [StreamChunk("reasoning", "thought"), StreamChunk("text", "ial")]
        text, reasoning = [], []
        for chunk in chunks:
            (text if chunk.kind == "text" else reasoning).append(chunk.text)
            try:
                on_chunk(chunk)
            except ChatPersistenceError:
                break
        status = "cancelled" if self.mode == "cancelled" or self.cancelled else "complete"
        return {"status": status, "text": "".join(text), "reasoning": "".join(reasoning),
                "metrics": {"output_tokens": len(text)}}


class FailingStore(Store):
    fail_writes = False

    def save_message(self, conversation_id, role, text, status="complete", reasoning="",
                     metadata=None, message_id=None):
        if self.fail_writes and role == "assistant" and message_id is not None:
            raise OSError("disk full")
        return super().save_message(conversation_id, role, text, status,
                                    reasoning, metadata, message_id)


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "studio.sqlite")
        self.session = FakeSession()
        self.events = []
        self.service = ChatService(self.store, self.session,
                                   lambda event, payload: self.events.append((event, payload)))

    def test_two_turns_persist_in_order(self):
        first = self.service.send(None, "First?", max_tokens=32)
        second = self.service.send(first["conversation_id"], "Second?", max_tokens=32)
        rows = second["messages"]
        self.assertEqual([(row["role"], row["text"], row["status"]) for row in rows],
                         [("user", "First?", "complete"),
                          ("assistant", "partial", "complete"),
                          ("user", "Second?", "complete"),
                          ("assistant", "partial", "complete")])
        self.assertEqual(rows[1]["reasoning"], "thought")
        self.assertEqual([message["content"] for message in self.session.requests[1]],
                         ["First?", "partial", "Second?"])
        self.assertEqual(len([event for event, _ in self.events if event == "chat_started"]), 2)

    def test_automatic_budget_follows_active_model_and_remaining_history(self):
        first = self.service.send(None, "First?")
        self.assertEqual(self.session.budgets[-1], 4096 - self.service._estimate(self.session.requests[-1]))
        self.session.snapshot.update(context=32768, effective_context=32768,
                                     config={"model": "thinking", "reasoning_budget": 4096})
        second = self.service.send(first["conversation_id"], "Second?")
        expected = 32768 - self.service._estimate(self.session.requests[-1])
        self.assertEqual(self.session.budgets[-1], expected)
        self.assertGreater(expected, 4096 + 2048)
        meta = second["messages"][-1]["metadata"]
        self.assertEqual(meta["requested_max_tokens"], expected)
        self.assertEqual(meta["max_tokens_policy"], "remaining_context")
        self.assertEqual(meta["config"]["model"], "thinking")

    def test_automatic_budget_respects_effective_context_and_rejects_full_history(self):
        self.session.snapshot.update(context=32768, effective_context=4096)
        result = self.service.send(None, "Hello")
        self.assertEqual(self.session.budgets[-1], 4096 - self.service._estimate(self.session.requests[-1]))
        count = len(result["messages"])
        with self.assertRaises(ChatContextOverflow):
            self.service.send(result["conversation_id"], "x" * 4096)
        self.assertEqual(len(self.store.messages(result["conversation_id"])), count)
        self.assertEqual(len(self.session.requests), 1)

    def test_explicit_budget_is_kept_and_validated(self):
        result = self.service.send(None, "Hello", max_tokens=32)
        self.assertEqual(self.session.budgets[-1], 32)
        self.assertEqual(result["messages"][-1]["metadata"]["max_tokens_policy"], "explicit")
        for value in (0, -1, True, 2.5, "32"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.send(None, "Hello", max_tokens=value)

    def test_reasoning_only_attempt_is_saved_but_not_replayed(self):
        conversation = self.store.create_conversation("Thinking")["id"]
        self.store.save_message(conversation, "user", "First?")
        self.store.save_message(conversation, "assistant", "", reasoning="unfinished thought",
                                metadata={"done_reason": "length"})
        result = self.service.send(conversation, "Next?")
        self.assertEqual([m["content"] for m in self.session.requests[-1]], ["First?", "Next?"])
        self.assertEqual(result["messages"][1]["reasoning"], "unfinished thought")

    def test_cancelled_partial_is_saved_and_excluded_from_next_prompt(self):
        self.session.mode = "cancelled"
        first = self.service.send(None, "First?", max_tokens=32)
        self.assertEqual(first["messages"][-1]["status"], "cancelled")
        self.assertEqual(first["messages"][-1]["text"], "part")
        self.session.mode = "complete"
        self.service.send(first["conversation_id"], "Second?", max_tokens=32)
        self.assertEqual([message["content"] for message in self.session.requests[1]],
                         ["First?", "Second?"])

    def test_checkpoint_failure_cancels_and_emits_unsaved_partial(self):
        store = FailingStore(Path(self.temp.name) / "failure.sqlite")
        ticks = iter((0.0, 1.2))
        service = ChatService(store, self.session,
                              lambda event, payload: self.events.append((event, payload)),
                              clock=lambda: next(ticks))
        # The placeholder write succeeds; subsequent checkpoint/final writes fail.
        original_save = store.save_message
        def fail_after_placeholder(*args, **kwargs):
            row = original_save(*args, **kwargs)
            if args[1] == "assistant" and args[3] == "streaming":
                store.fail_writes = True
            return row
        store.save_message = fail_after_placeholder
        with self.assertRaises(ChatPersistenceError) as raised:
            service.send(None, "Hello", max_tokens=32)
        self.assertFalse(raised.exception.saved)
        self.assertTrue(self.session.cancelled)
        unsaved = [payload for event, payload in self.events if event == "chat_unsaved"]
        self.assertEqual(unsaved[-1]["text"], "part")
        self.assertIn("disk full", unsaved[-1]["save_error"])
        rows = store.messages(unsaved[-1]["conversation_id"])
        self.assertEqual(rows[0]["status"], "complete")
        self.assertEqual(rows[1]["status"], "streaming")

    def test_image_sent_and_replayed_from_managed_reference(self):
        image = Path(self.temp.name) / "photo.png"
        image.write_bytes(png_pixel())
        item = import_image(image, Path(self.temp.name))
        self.session.snapshot.update(vision_available=True, context=16384,
                                     effective_context=16384)
        first = self.service.send(None, "Look", max_tokens=32, attachments=[item])
        user = first["messages"][0]
        self.assertEqual(user["metadata"]["attachments"][0]["path"],
                         f"attachments/{item['id']}.png")
        self.assertNotIn("images", user)
        self.assertEqual(base64.b64decode(self.session.requests[0][0]["images"][0]), png_pixel())
        self.service.send(first["conversation_id"], "Next", max_tokens=32)
        self.assertEqual(base64.b64decode(self.session.requests[1][0]["images"][0]), png_pixel())
        self.assertNotIn("images", self.session.requests[1][-1])

    def test_image_requires_verified_vision_and_missing_history_fails_before_save(self):
        image = Path(self.temp.name) / "photo.png"
        image.write_bytes(png_pixel())
        item = import_image(image, Path(self.temp.name))
        with self.assertRaisesRegex(AttachmentError, "поддержку изображений"):
            self.service.send(None, "Look", max_tokens=32, attachments=[item])
        self.assertEqual(self.store.conversations(), [])
        self.session.snapshot.update(vision_available=True, context=16384,
                                     effective_context=16384)
        first = self.service.send(None, "Look", max_tokens=32, attachments=[item])
        Path(item["path"]).unlink()
        with self.assertRaisesRegex(AttachmentError, "отсутствует"):
            self.service.send(first["conversation_id"], "Next", max_tokens=32)
        self.assertEqual(len(self.store.messages(first["conversation_id"])), 2)

    def test_image_budget_rejects_before_persistence(self):
        image = Path(self.temp.name) / "photo.png"
        image.write_bytes(png_pixel())
        item = import_image(image, Path(self.temp.name))
        self.session.snapshot["vision_available"] = True
        with self.assertRaisesRegex(ValueError, "context"):
            self.service.send(None, "Look", max_tokens=32, attachments=[item])
        self.assertEqual(self.store.conversations(), [])


if __name__ == "__main__":
    unittest.main()
