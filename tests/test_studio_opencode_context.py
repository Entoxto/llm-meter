"""Context survives real OpenAI requests; no model, GPU or user data required."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_studio.configuration import LaunchConfig
from model_studio.session import SessionController
from model_studio.catalog import Catalog
from model_studio.storage import Store
from model_studio.integrations.opencode import connection_config, executable


class ContextServer:
    def __init__(self):
        self.models = {"test-32k:latest": 32768}
        self.loaded = None
        self.context = None
        self.chat_requests = []
        self.creates = []
        self.deletes = []
        self.fail_create = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send_json(self, value, code=200):
                body = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def body(self):
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

            def do_GET(self):
                if self.path == "/api/version":
                    return self.send_json({"version": "0.34.3"})
                if self.path == "/api/tags":
                    return self.send_json({"models": [{"name": name, "digest": "digest-" + name,
                        "size": 1000, "details": {"family": "qwen3", "parameter_size": "1B"}}
                        for name in owner.models]})
                if self.path == "/api/ps":
                    return self.send_json({"models": [{"name": owner.loaded, "context_length": owner.context,
                        "size": 1000, "size_vram": 1000}] if owner.loaded else []})
                if self.path == "/v1/models":
                    return self.send_json({"object": "list", "data": [{"id": name, "object": "model",
                        "created": 0, "owned_by": "ollama"} for name in owner.models]})
                self.send_json({"error": "missing"}, 404)

            def do_DELETE(self):
                name = self.body()["model"]
                owner.deletes.append(name)
                owner.models.pop(name, None)
                self.send_json({})

            def do_POST(self):
                payload = self.body()
                name = payload.get("model")
                if self.path == "/api/create":
                    owner.creates.append(payload)
                    if owner.fail_create:
                        return self.send_json({"error": "creation failed"}, 500)
                    owner.models[name] = payload["parameters"]["num_ctx"]
                    return self.send_json({"status": "success"})
                if name not in owner.models:
                    return self.send_json({"error": "model not found"}, 404)
                if self.path == "/api/show":
                    return self.send_json({"parameters": f"num_ctx {owner.models[name]}\ntemperature 0.6",
                        "details": {"family": "qwen3", "parameter_size": "1B"},
                        "capabilities": ["completion", "tools"],
                        "model_info": {"general.architecture": "qwen3", "qwen3.context_length": 131072}})
                if self.path == "/api/generate":
                    if payload.get("keep_alive") == 0:
                        owner.loaded = None
                    else:
                        owner.loaded = name
                        owner.context = payload.get("options", {}).get("num_ctx", owner.models[name])
                    return self.send_json({"done": True})
                if self.path == "/v1/chat/completions":
                    owner.chat_requests.append(payload)
                    # Like Ollama's OpenAI adapter: ignore options.num_ctx and use defaults.
                    owner.loaded, owner.context = name, owner.models[name]
                    if not payload.get("stream"):
                        return self.send_json({"id": "test", "object": "chat.completion", "created": 1,
                            "model": name, "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9}})
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for delta, finish in (({"role": "assistant", "content": "ok"}, None), ({}, "stop")):
                        chunk = {"id": "test", "object": "chat.completion.chunk", "created": 1, "model": name,
                            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                self.send_json({"error": "missing"}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class ContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = ContextServer()
        self.addCleanup(self.server.close)
        self.core = SessionController(self.temp.name, lambda *_: None)
        self.config = LaunchConfig(model="test-32k:latest", backend="ollama", managed=False,
            host=self.server.host, context=65536)
        self.core.start(self.config)

    def request_chat(self, model):
        req = Request(self.server.host + "/v1/chat/completions", data=json.dumps({
            "model": model, "messages": [{"role": "user", "content": "hello"}],
            "options": {"num_ctx": 65536}}).encode(), headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_context_stays_pinned_across_external_messages_and_cleanup(self):
        self.request_chat(self.config.model)
        self.assertEqual(self.server.context, 32768)  # Reproduce the regression first.
        self.assertEqual(self.core.refresh_status()["status"], "context_changed")
        self.core.start(self.config)
        prepared = self.core.prepare_external_client()
        profile = prepared["model_id"]
        self.assertNotEqual(profile, self.config.model)
        self.assertEqual(prepared["config"], self.config.to_dict())
        for _ in range(2):
            self.request_chat(profile)
            self.assertEqual(self.server.context, 65536)
            self.assertEqual(self.core.refresh_status()["status"], "ready")
        self.assertEqual(self.core.prepare_external_client()["model_id"], profile)
        self.assertEqual(len(self.server.creates), 1)
        store = Store(Path(self.temp.name) / "catalog.db")
        catalog = Catalog(store)
        models = catalog.scan({"backend_hosts": {"Ollama": self.server.host}})
        self.assertEqual([m["tag"] for m in models], [self.config.model])
        self.core.unload()
        self.assertEqual(self.server.models, {self.config.model: 32768})
        self.assertEqual(self.server.deletes, [profile])

    def test_profile_creation_failure_preserves_original_session(self):
        self.server.fail_create = True
        with self.assertRaisesRegex(RuntimeError, "creation failed"):
            self.core.prepare_external_client()
        self.assertEqual(self.core.snapshot["status"], "ready")
        self.assertEqual(self.server.loaded, self.config.model)
        self.assertIsNone(self.core.client.context_profile)
        self.assertIsNone(self.core.snapshot["operation_id"])
        self.server.fail_create = False
        self.core.prepare_external_client()
        self.core.unload()

    @unittest.skipUnless(os.environ.get("MODEL_STUDIO_LIVE_OPENCODE_CONTEXT") == "1",
                         "requires installed OpenCode; uses only a fake Ollama server")
    def test_installed_opencode_sends_profile_on_every_turn(self):
        prepared = self.core.prepare_external_client()
        folder = Path(self.temp.name)
        config_path = folder / "config" / "opencode" / "opencode.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(json.dumps(connection_config(prepared)), encoding="utf-8")
        env = os.environ.copy()
        env.update(XDG_CONFIG_HOME=str(folder / "config"), XDG_DATA_HOME=str(folder / "data"),
                   XDG_STATE_HOME=str(folder / "state"), OPENCODE_DISABLE_PROJECT_CONFIG="1")
        for key in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT"):
            env.pop(key, None)
        for turn in range(2):
            if turn:
                # New studio launch, same project/conversation: its stable model
                # selection must resolve to the new private runtime tag.
                old_profile = prepared["model_id"]
                self.core.unload()
                self.core.start(self.config)
                prepared = self.core.prepare_external_client()
                self.assertNotEqual(prepared["model_id"], old_profile)
                config_path.write_text(json.dumps(connection_config(prepared)), encoding="utf-8")
            before = len(self.server.chat_requests)
            args = [str(executable()), "run", "--standalone", "--format", "json"]
            if turn:
                args.append("--continue")
            result = subprocess.run([*args, "Say ok. Do not use tools."], cwd=folder, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, (result.stderr + result.stdout)[-3000:])
            self.assertGreater(len(self.server.chat_requests), before, result.stdout[-2000:])
            self.assertTrue(all(r["model"] == prepared["model_id"] for r in self.server.chat_requests[before:]))
            self.assertEqual(self.server.context, 65536)
            self.assertEqual(self.core.refresh_status()["status"], "ready")
        self.core.unload()


if __name__ == "__main__":
    unittest.main()
