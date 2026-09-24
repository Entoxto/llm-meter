"""Local HTTP contract checks using real cancellable transport, no model/GPU."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_studio.configuration import LaunchConfig
from model_studio.session import SessionController


class FakeServer:
    def __init__(self, backend):
        self.backend = backend
        self.blocking = threading.Event()
        self.release = threading.Event()
        self.unloads = 0
        self.preloads = 0
        self.loaded = False
        self.allow_preload = True
        self.request_keep_alive = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def json(self, value):
                data = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/version":
                    return self.json({"version": "fake"})
                if self.path == "/api/tags":
                    return self.json({"models": [{"name": "demo", "digest": "abc", "size": 1000}]})
                if self.path == "/api/ps":
                    return self.json({"models": [{"name": "demo", "context_length": 4096,
                                                    "size": 1000, "size_vram": 500}]
                                      if owner.loaded else []})
                if self.path == "/health":
                    return self.json({"status": "ok"})
                if self.path == "/v1/models":
                    return self.json({"data": [{"id": "demo", "meta": {"n_ctx": 4096}}]})
                if self.path.startswith("/props?"):
                    return self.json({"default_generation_settings": {"n_ctx": 4096}})
                self.send_error(404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.loads(body or b"{}")
                if self.path == "/api/show":
                    return self.json({"details": {}, "capabilities": ["completion"]})
                if self.path == "/api/generate" and payload.get("keep_alive") == 0:
                    owner.unloads += 1
                    owner.loaded = False
                    return self.json({"done": True})
                if self.path == "/api/generate":
                    owner.request_keep_alive.append(payload.get("keep_alive"))
                    if payload.get("prompt") == "":
                        owner.preloads += 1
                        owner.loaded = owner.allow_preload
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write((json.dumps({"response": "x", "done": False}) + "\n").encode())
                    self.wfile.write((json.dumps({"done": True, "eval_count": payload["options"]["num_predict"],
                                                  "eval_duration": 1_000_000_000,
                                                  "prompt_eval_count": 20,
                                                  "prompt_eval_duration": 100_000_000}) + "\n").encode())
                    return
                if self.path == "/api/chat":
                    owner.request_keep_alive.append(payload.get("keep_alive"))
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"message":{"content":"hello"},"done":false}\n')
                    self.wfile.flush()
                    if payload["messages"][-1]["content"] == "block":
                        owner.blocking.set()
                        owner.release.wait(3)
                    try:
                        self.wfile.write(b'{"done":true,"eval_count":1,"eval_duration":1000000000}\n')
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if self.path == "/v1/chat/completions":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
                    return  # Deliberately missing data: [DONE]
                self.send_error(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def host(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class RealTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_ollama_chat_cancel_benchmark_and_unload(self):
        server = FakeServer("ollama")
        self.addCleanup(server.close)
        events = []
        first_text = threading.Event()
        def emit(event, payload):
            events.append((event, payload))
            if event == "text":
                first_text.set()
        session = SessionController(self.temp.name, emit)
        config = LaunchConfig(model="demo", backend="ollama", managed=False,
                              host=server.host, context=4096)
        self.assertEqual(session.start(config)["status"], "ready")
        self.assertEqual(server.preloads, 1)
        self.assertEqual(session.snapshot["effective_context"], 4096)
        completed = session.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(completed["status"], "complete")
        self.assertEqual(completed["text"], "hello")
        first_text.clear()
        results = []
        thread = threading.Thread(target=lambda: results.append(session.chat(
            [{"role": "user", "content": "block"}])))
        thread.start()
        self.assertTrue(server.blocking.wait(2))
        # Server write alone does not prove the client consumed the chunk.
        self.assertTrue(first_text.wait(2))
        self.assertTrue(session.cancel())
        thread.join(3)
        server.release.set()
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["status"], "cancelled")
        self.assertEqual(results[0]["text"], "hello")
        report = session.benchmark(runs=1, tokens=8)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["runs"][0]["tokens_per_second"], 8)
        self.assertEqual(set(server.request_keep_alive), {-1})
        self.assertEqual(session.unload()["status"], "stopped")
        self.assertEqual(server.unloads, 1)
        self.assertTrue(any(event == "text" for event, _ in events))

    def test_ollama_does_not_mark_unloaded_model_ready(self):
        server = FakeServer("ollama")
        self.addCleanup(server.close)
        session = SessionController(self.temp.name, lambda *_: None)
        config = LaunchConfig(model="demo", backend="ollama", managed=False,
                              host=server.host, context=4096)
        server.allow_preload = False
        with self.assertRaises(RuntimeError):
            session.start(config)
        self.assertEqual(session.snapshot["status"], "failed")
        server.allow_preload = True
        session.start(config)
        server.loaded = False  # Simulate external eviction after startup.
        self.assertEqual(session.refresh_status()["status"], "disconnected")
        self.assertEqual(session.unload()["status"], "stopped")

    def test_llama_truncated_sse_keeps_healthy_external_session(self):
        server = FakeServer("llama.cpp")
        self.addCleanup(server.close)
        session = SessionController(self.temp.name, lambda *_: None)
        config = LaunchConfig(model="demo", backend="llama.cpp", managed=False,
                              host=server.host, context=4096)
        self.assertEqual(session.start(config)["status"], "ready")
        result = session.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["text"], "partial")
        self.assertIn("[DONE]", result["metrics"]["error"])
        self.assertEqual(session.refresh_status()["status"], "ready")
        session.unload()


if __name__ == "__main__":
    unittest.main()
