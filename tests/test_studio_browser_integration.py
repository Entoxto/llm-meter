"""Opt-in, isolated Studio -> OpenCode V2 -> official browser host smoke test.

Run only after packaging the companion:
  $env:MODEL_STUDIO_LIVE_BROWSER_INTEGRATION='1'
  python -m unittest tests.test_studio_browser_integration -v
The model, website, database and Studio data directory are all temporary.
"""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_studio_opencode_context import ContextServer
from model_studio.configuration import LaunchConfig
from model_studio.integrations import opencode
from model_studio.integrations.browser_host import available as companion_available
from model_studio.platform.paths import ensure_data_dirs
from model_studio.session import SessionController
from model_studio.storage import Store

try:
    from PySide6.QtWidgets import QApplication
    from model_studio.desktop.controllers import Studio
except ImportError:
    QApplication = None
    Studio = None


class _LocalPage:
    def __init__(self):
        self.requests = 0
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                owner.requests += 1
                body = b"<html><title>Studio browser proof</title><body><h1>HTTP fixture proof 417</h1></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class _ScriptedModel(ContextServer):
    """Ollama control endpoints from ContextServer plus scripted OpenAI tool calls."""

    def __init__(self, page_url: str):
        super().__init__()
        self.page_url = page_url
        self.tool_requests = []
        original = self.server.RequestHandlerClass.do_POST
        owner = self

        def do_POST(handler):
            if handler.path != "/v1/chat/completions":
                return original(handler)
            payload = handler.body()
            owner.chat_requests.append(payload)
            owner.loaded = payload["model"]
            owner.context = owner.models.get(payload["model"])
            messages = payload.get("messages") or []
            tools = payload.get("tools") or []
            tool_name = next((tool.get("function", {}).get("name") for tool in tools
                              if tool.get("function", {}).get("name") == "execute"), None)
            owner.tool_requests.append({"tools": tools, "messages": messages})
            latest_user = max((index for index, message in enumerate(messages)
                               if message.get("role") == "user"), default=-1)
            already_called = any(message.get("role") == "tool" for message in messages[latest_user + 1:])
            if tool_name and not already_called:
                code = (
                    "const before = await tools.browser.tabs.list({}); "
                    "const tab = await tools.browser.tabs.open({url:" + json.dumps(page_url) + ",focus:false}); "
                    "const proof = await tools.browser.snapshot({tabID:tab.id}); "
                    "return JSON.stringify({before:before.tabs.length,content:proof.content});"
                )
                delta = {"role": "assistant", "tool_calls": [{"index": 0,
                    "id": "call_browser_proof", "type": "function",
                    "function": {"name": "execute", "arguments": json.dumps({"code": code})}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"role": "assistant", "content": "Browser check finished."}, "stop"
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            for chunk in ((delta, None), ({}, finish)):
                value = {"id": "studio-browser-proof", "object": "chat.completion.chunk",
                         "created": 1, "model": payload["model"],
                         "choices": [{"index": 0, "delta": chunk[0], "finish_reason": chunk[1]}]}
                handler.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
            handler.wfile.write(b"data: [DONE]\n\n")

        self.server.RequestHandlerClass.do_POST = do_POST


@unittest.skipUnless(os.environ.get("MODEL_STUDIO_LIVE_BROWSER_INTEGRATION") == "1",
                     "opt-in: requires installed OpenCode V2 and packaged browser companion")
@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class StudioBrowserIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.assertTrue(companion_available(), "Package browser-host/ModelStudioBrowser.exe first")
        self.temporary = tempfile.TemporaryDirectory(prefix="studio-browser-e2e-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.page = _LocalPage()
        self.addCleanup(self.page.close)
        self.model = _ScriptedModel(self.page.url)
        self.addCleanup(self.model.close)
        isolated = {"MODEL_STUDIO_DATA_DIR": str(self.root / "studio-data"),
                    "XDG_DATA_HOME": str(self.root / "xdg-data"),
                    "XDG_STATE_HOME": str(self.root / "xdg-state"),
                    "XDG_CACHE_HOME": str(self.root / "xdg-cache"),
                    "OPENCODE_DB": str(self.root / "opencode.sqlite"),
                    "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}
        environment = patch.dict(os.environ, isolated)
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(opencode.stop_web)
        self.urls = []
        browser = patch("model_studio.integrations.opencode.webbrowser.open",
                        side_effect=lambda url: self.urls.append(url) or True)
        browser.start()
        self.addCleanup(browser.stop)
        self.paths = ensure_data_dirs(self.root / "studio-data")
        self.config = LaunchConfig(model="test-32k:latest", backend="ollama", managed=False,
                                   host=self.model.host, context=65536)
        self._construct_studio()

    def _construct_studio(self):
        self.studio = Studio(Store(self.paths["database"]), self.paths, self.root, initialize=False)
        self.studio._timer.stop()
        self.addCleanup(self.studio.deleteLater)
        self.addCleanup(self.studio.workers.shutdown)
        self.core = SessionController(self.paths["logs"], lambda *_: None)
        self.core.start(self.config)
        self.addCleanup(self.core.unload)
        self.studio.core = self.core
        self.studio._values["session"] = self.core.snapshot
        self.studio._values["selectedProject"] = {"path": str(self.project)}
        self.studio._values["settings"] = {"opencode_exe": str(opencode.executable())}

    def _open_from_studio(self, browser_status="ready"):
        self.studio._values["session"] = self.core.snapshot
        self.studio.openOpenCode()
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            self.studio._pump()
            if not self.studio.busy:
                break
            time.sleep(.05)
        self.assertFalse(self.studio.busy, "Studio OpenCode worker timed out")
        self.assertFalse(self.studio.error, self.studio.error)
        result = self.studio.openCode
        self.assertEqual(result["browser"]["status"], browser_status)
        self.assertEqual(result["mode"], "web")
        return result

    def _api(self, result, method, path, payload=None):
        link = self.urls[-1]
        encoded = urlsplit(link).fragment
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        authorization = base64.b64encode((data["username"] + ":" + data["password"]).encode()).decode()
        request = Request(result["url"] + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Authorization": "Basic " + authorization,
                                   "Content-Type": "application/json"}, method=method)
        with urlopen(request, timeout=45) as response:
            body = response.read()
        return json.loads(body) if body else None

    def _new_session(self, result, permissions=None):
        payload = {"location": {"directory": str(self.project)}}
        if permissions is not None:
            payload["permissions"] = permissions
        return self._api(result, "POST", "/api/session", payload)["data"]

    def _wait_for_attachment(self, result, session_id, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            session = self._api(result, "GET", "/api/session/" + session_id)["data"]
            if (session.get("metadata") or {}).get("model_studio_browser_host"):
                return session
            time.sleep(.15)
        self.fail("Native browser host did not attach the new session")

    def _run_prompt(self, result, session_id, *, after_requests=-1):
        self._api(result, "POST", "/api/session/" + session_id + "/prompt",
                  {"text": "Use the browser to inspect the local proof page."})
        return self._wait_message(result, session_id, "HTTP fixture proof 417",
                                  after_requests=after_requests)

    def _wait_message(self, result, session_id, marker, *, after_requests=-1):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            messages = self._api(result, "GET", "/api/session/" + session_id + "/message")
            data = messages.get("data") or []
            dump = json.dumps(data, ensure_ascii=False)
            if (marker in dump and self.page.requests > after_requests) or "browser.disconnected" in dump:
                return dump
            time.sleep(.2)
        self.fail("Native execute result did not arrive")

    def test_studio_launch_native_browser_and_relaunch_same_isolated_db(self):
        first = self._open_from_studio()
        session = self._new_session(first)
        # Submit immediately: the private prompt hook must wait for attachment
        # before SessionContext snapshots its browser permission.
        result = self._run_prompt(first, session["id"])
        self._wait_for_attachment(first, session["id"])
        self.assertIn("HTTP fixture proof 417", result)
        self.assertNotIn("browser.disconnected", result)
        self.assertTrue(any(any(tool.get("function", {}).get("name") == "execute"
                                for tool in request["tools"]) for request in self.model.tool_requests))
        self.assertEqual(self.model.context, 65536)
        first_requests = self.page.requests
        self.assertGreater(first_requests, 0)
        self.studio.stopOpenCode()
        deadline = time.monotonic() + 15
        while self.studio.busy and time.monotonic() < deadline:
            self.studio._pump()
            time.sleep(.05)
        self.assertFalse(self.studio.busy)
        self.studio.workers.shutdown()
        self.core.unload()
        self.studio.deleteLater()
        self._construct_studio()
        second = self._open_from_studio()
        self.assertEqual(os.environ["OPENCODE_DB"], str(self.root / "opencode.sqlite"))
        self._wait_for_attachment(second, session["id"])
        resumed = self._run_prompt(second, session["id"], after_requests=first_requests)
        self.assertIn("HTTP fixture proof 417", resumed)
        self.assertNotIn("browser.disconnected", resumed)
        resumed_requests = self.page.requests
        self.assertGreater(resumed_requests, first_requests)
        session = self._new_session(second)
        result = self._run_prompt(second, session["id"], after_requests=resumed_requests)
        self._wait_for_attachment(second, session["id"])
        self.assertIn("HTTP fixture proof 417", result)
        self.assertNotIn("browser.disconnected", result)
        self.assertGreater(self.page.requests, resumed_requests)

    def test_explicit_session_browser_deny_survives_attachment(self):
        result = self._open_from_studio()
        deny = {"action": "browser", "resource": "*", "effect": "deny"}
        session = self._new_session(result, [deny])
        attached = self._wait_for_attachment(result, session["id"])
        self.assertEqual(attached["permissions"][-1], deny)
        self.assertEqual(attached["permissions"][0]["effect"], "allow")
        self._api(result, "POST", "/api/session/" + session["id"] + "/prompt",
                  {"text": "Inspect the local browser page."})
        messages = self._wait_message(result, session["id"], "Browser check finished")
        self.assertNotIn("HTTP fixture proof 417", messages)
        self.assertNotIn("browser.disconnected", messages)

    def test_missing_companion_keeps_browser_tools_disabled(self):
        with patch("model_studio.integrations.opencode.browser_host.available", return_value=False):
            result = self._open_from_studio("unavailable")
        session = self._new_session(result)
        self._api(result, "POST", "/api/session/" + session["id"] + "/prompt",
                  {"text": "Say ready."})
        deadline = time.monotonic() + 20
        while not self.model.tool_requests and time.monotonic() < deadline:
            time.sleep(.1)
        self.assertTrue(self.model.tool_requests, "Mock model was not called")
        self.assertNotIn("opencode.browser", json.dumps(self.model.tool_requests))
        self.assertNotIn("browser.disconnected", json.dumps(self.model.tool_requests))

    def test_owned_companion_exit_removes_native_browser_plugin(self):
        result = self._open_from_studio()
        owned = opencode._owned_web["browser_host"].process
        self.assertIsNotNone(owned)
        owned.terminate()  # The process created by this isolated test only.
        owned.wait(timeout=10)
        deadline = time.monotonic() + 15
        disabled = None
        while time.monotonic() < deadline:
            files = list((self.root / "studio-data" / "opencode" / "launches").glob(
                "*/opencode/opencode.json"))
            if files:
                disabled = json.loads(files[-1].read_text(encoding="utf-8"))
            if disabled and disabled.get("plugins") == ["-opencode.browser"]:
                break
            time.sleep(.1)
        self.assertEqual(disabled.get("plugins") if disabled else None, ["-opencode.browser"])
        self.assertEqual(opencode.status()["browser"]["status"], "unavailable")
        entries = self._api(result, "GET", "/api/config")
        self.assertTrue(any(entry.get("info", {}).get("plugins") == ["-opencode.browser"]
                            for entry in entries), "Reloaded server config did not remove browser plugin")


if __name__ == "__main__":
    unittest.main()
