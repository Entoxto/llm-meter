"""Session invariants without a server or GPU."""
from __future__ import annotations

import sys
import base64
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engine import Cancelled
from model_studio.configuration import LaunchConfig
from model_studio.domain import SessionBusy, StreamChunk
from model_studio.session import SessionController


class FakeBackend:
    backend = "llama.cpp"
    host = "http://127.0.0.1:8081"
    context = 4096
    local = False
    model_info = {"context_limit": 4096}

    def __init__(self, *args, **kwargs):
        self.started = threading.Event()
        self.cancelled = False
        self.fail_chat = False
        self.connected = True

    def prepare(self, model):
        return {"server_version": "fake"}

    def cancel(self):
        self.cancelled = True

    def chat(self, model, messages, max_tokens, temperature, stop):
        yield StreamChunk("text", "partial")
        self.started.set()
        if messages[0]["content"] == "block":
            while not stop.wait(.01):
                pass
            raise Cancelled()
        if self.fail_chat:
            raise RuntimeError("truncated stream")
        yield StreamChunk("reasoning", "why")
        yield {"output_tokens": 2, "tokens_per_second": None}

    def generate(self, model, prompt, tokens, stop):
        if stop.is_set():
            raise Cancelled()
        return {"tokens": tokens, "output_tokens": tokens,
                "generation_seconds": 1, "tokens_per_second": tokens}

    def resident(self, model):
        return {"context_length": 4096}

    def loaded_models(self):
        return [{"name": "C:/models/a.gguf", "context_length": 4096}]

    def request(self, path, timeout=2):
        if not self.connected:
            raise RuntimeError("connection lost")
        return {}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.events = []
        self.patcher = patch("model_studio.session.LlamaCppBackend", FakeBackend)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.temp.cleanup)
        self.session = SessionController(self.temp.name, lambda event, payload: self.events.append((event, payload)))
        self.config = LaunchConfig(model="C:/models/a.gguf", managed=False, context=4096)

    def start(self):
        self.session.start(self.config)

    def test_active_configuration_and_chat(self):
        self.start()
        desired = LaunchConfig(model="C:/models/b.gguf", managed=False, context=8192)
        self.assertEqual(desired.context, 8192)
        self.assertEqual(self.session.snapshot["context"], 4096)
        result = self.session.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["text"], "partial")
        self.assertEqual(result["reasoning"], "why")
        self.assertEqual(self.session.snapshot["busy"], "idle")
        self.assertIsNone(self.session.snapshot["operation_id"])
        self.assertTrue(any(event == "text" and payload["text"] == "partial" for event, payload in self.events))

    def test_cancel_does_not_clear_during_request(self):
        self.start()
        result = []
        worker = threading.Thread(target=lambda: result.append(self.session.chat(
            [{"role": "user", "content": "block"}])))
        worker.start()
        self.assertTrue(self.session.client.started.wait(1))
        self.assertEqual(self.session.snapshot["busy"], "chat")
        with self.assertRaises(SessionBusy):
            self.session.benchmark(runs=1)
        self.assertTrue(self.session.cancel())
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["status"], "cancelled")
        self.assertEqual(result[0]["text"], "partial")
        self.assertEqual(self.session.snapshot["status"], "ready")
        self.assertFalse(self.session.cancel())

    def test_chat_error_and_benchmark_cleanup(self):
        self.start()
        self.session.client.fail_chat = True
        result = self.session.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(result["status"], "error")
        self.assertIn("truncated", result["metrics"]["error"])
        report = self.session.benchmark(runs=2, tokens=8)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(report["runs"]), 2)
        self.assertEqual(report["config"]["context"], 4096)
        self.assertEqual(self.session.snapshot["busy"], "idle")

    def test_external_llama_unload_only_disconnects(self):
        self.start()
        self.assertEqual(self.session.unload()["status"], "stopped")
        self.assertIsNone(self.session.client)

    def test_lost_external_connection_marks_disconnected(self):
        self.start()
        self.session.client.fail_chat = True
        self.session.client.connected = False
        self.assertEqual(self.session.chat([{"role": "user", "content": "hello"}])["status"], "error")
        self.assertEqual(self.session.snapshot["status"], "disconnected")
        self.assertEqual(self.session.unload()["status"], "stopped")

    def test_emitter_failure_still_releases_busy_state(self):
        self.start()
        def broken_emit(event, payload):
            if event in ("text", "error", "chat_finished"):
                raise RuntimeError("UI delivery failed")
        self.session.emit = broken_emit
        with self.assertRaises(RuntimeError):
            self.session.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(self.session.snapshot["busy"], "idle")

    def test_refresh_marks_unexpected_owned_exit_failed(self):
        self.start()
        class ExitedOwner:
            running = False
        self.session._owned = ExitedOwner()
        self.assertEqual(self.session.refresh_status()["status"], "failed")
        self.assertIn("exited", self.session.snapshot["error"])

    def test_conflicting_flags_rejected(self):
        with self.assertRaises(ValueError):
            LaunchConfig(model="C:/a.gguf", executable="C:/server.exe", extra_args=("--port", "8082"))
        with self.assertRaises(ValueError):
            LaunchConfig(model="C:/a.gguf", executable="C:/server.exe", mtp=True)
        with self.assertRaises(ValueError):
            LaunchConfig(model="C:/a.gguf", executable="C:/server.exe", kv_type="q8_0")
        with self.assertRaises(ValueError):
            LaunchConfig(model="C:/a.gguf", executable="C:/server.exe", reasoning="off")
        with self.assertRaises(ValueError):
            LaunchConfig(model="C:/a.gguf", executable="C:/server.exe",
                         extra_args=("--mmproj", "other.gguf"))
        with self.assertRaises(ValueError):
            LaunchConfig(model="tag", backend="ollama", managed=False, mmproj="image.gguf")
        with self.assertRaises(ValueError):
            LaunchConfig(model="C:/a.gguf", managed=False, mmproj="image.gguf")
        self.assertEqual(LaunchConfig.from_dict(self.config.to_dict()).mmproj, "")

    def test_image_chat_requires_confirmed_vision(self):
        self.start()
        image = base64.b64encode(b"\x89PNG\r\n\x1a\nunit-test").decode("ascii")
        messages = [{"role": "user", "content": "hello", "images": [image]}]
        with self.assertRaisesRegex(ValueError, "not confirmed"):
            self.session.chat(messages)
        self.session.client.vision_available = True
        self.assertTrue(self.session.snapshot["vision_available"])
        self.assertEqual(self.session.chat(messages)["status"], "complete")
        with self.assertRaisesRegex(ValueError, "PNG or JPEG"):
            self.session.chat([{"role": "user", "content": "hello", "images": [base64.b64encode(b"GIF89a").decode()]}])

    def test_research_lease_keeps_exclusivity_and_restores_with_new_token(self):
        with self.session.research_operation() as lease:
            self.assertEqual(lease.snapshot["busy"], "research")
            lease.start(self.config)
            self.assertEqual(lease.snapshot["busy"], "research")
            old_stop = lease.cancel_event
            self.assertTrue(lease.cancel())
            self.assertEqual(lease.benchmark(runs=1)["status"], "cancelled")
            self.assertTrue(old_stop.is_set())
            fresh_stop = lease.begin_restoration()
            self.assertIsNot(old_stop, fresh_stop)
            self.assertFalse(fresh_stop.is_set())
            lease.unload()
            self.assertEqual(lease.snapshot["busy"], "research")
        self.assertEqual(self.session.snapshot["busy"], "idle")
        self.assertEqual(self.session.snapshot["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
