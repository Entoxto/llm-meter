"""Durable chat flow with real SQLite and a controlled model stream."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_studio.chat import ChatPersistenceError, ChatService
from model_studio.domain import StreamChunk
from model_studio.storage.store import Store


class FakeSession:
    def __init__(self):
        self.snapshot = {"status": "ready", "context": 4096, "effective_context": 4096,
                         "session_id": "session-1", "config": {"model": "demo"}}
        self.requests = []
        self.cancelled = False
        self.mode = "complete"

    def cancel(self):
        self.cancelled = True
        return True

    def chat(self, messages, max_tokens=2048, on_chunk=None):
        self.requests.append(messages)
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


if __name__ == "__main__":
    unittest.main()
