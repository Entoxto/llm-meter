import base64
import json
import io
import os
import sys
from pathlib import Path
import socket
import subprocess
import secrets
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_studio.integrations.opencode import _web_url, connection_config, executable, launch


class OpenCodeTests(unittest.TestCase):
    def setUp(self):
        available = patch("model_studio.integrations.opencode.browser_host.available", return_value=False)
        available.start()
        self.addCleanup(available.stop)
        owned = patch("model_studio.integrations.opencode._owned_web", None)
        owned.start()
        self.addCleanup(owned.stop)

    def test_web_url_uses_owned_server_reported_ephemeral_port(self):
        process = Mock()
        process.poll.return_value = None
        process.stdout = io.StringIO("server listening on http://127.0.0.1:65432\n")
        with patch("model_studio.integrations.opencode.socket.create_connection") as connect:
            self.assertEqual(_web_url(process), "http://127.0.0.1:65432")
        connect.assert_called_once_with(("127.0.0.1", 65432), timeout=2)

    def test_v2_ollama_uses_runtime_model_and_native_provider(self):
        session = dict(status="ready", backend="ollama", model="outdated",
                       model_id="a/b:q4", host="http://127.0.0.1:11434/v1",
                       context=98304, effective_context=65536)
        config = connection_config(session)
        self.assertEqual(config["model"], {"providerID": "ollama", "model": "a/b:q4"})
        self.assertEqual(config["providers"]["ollama"]["settings"]["baseURL"],
                         "http://127.0.0.1:11434/v1")
        self.assertEqual(config["providers"]["ollama"]["models"]["a/b:q4"]["limit"],
                         {"context": 65536, "output": 4096})
        self.assertEqual(config["permissions"],
                         [{"action": "browser", "resource": "*", "effect": "deny"}])
        self.assertNotIn("provider", config)
        self.assertEqual(config["providers"]["ollama"]["package"], "@opencode/ai/providers/openai-compatible")

    def test_v2_llama_cpp_uses_explicit_compatible_provider(self):
        config = connection_config(dict(status="ready", backend="llama.cpp", model="test",
                                        host="http://127.0.0.1:8081", context=32768))
        self.assertEqual(config["model"], {"providerID": "studio-local", "model": "test"})
        provider = config["providers"]["studio-local"]
        self.assertEqual(provider["package"], "@opencode/ai/providers/openai-compatible")
        self.assertEqual(provider["settings"]["baseURL"], "http://127.0.0.1:8081/v1")
        self.assertIn("test", provider["models"])

    def test_stopped_session_cannot_open_client(self):
        with self.assertRaises(ValueError):
            connection_config({"status": "stopped"})

    def test_v2_launch_uses_private_app_config_without_project_writes(self):
        with tempfile.TemporaryDirectory(prefix="Проект & ") as folder, \
             tempfile.TemporaryDirectory(prefix="Studio-data-") as data:
            with patch.dict(os.environ, {"MODEL_STUDIO_DATA_DIR": data,
                                      "OPENCODE_CONFIG_CONTENT": "stale",
                                      "OPENCODE_CONFIG_DIR": "stale"}), \
                 patch("model_studio.integrations.opencode.executable", return_value=Path("C:/opencode.exe")), \
                 patch("model_studio.integrations.opencode.subprocess.run",
                       side_effect=[Mock(returncode=0, stdout="opencode v2.0.15"),
                                    Mock(returncode=0, stdout="--hostname --port")]), \
                 patch("model_studio.integrations.opencode.subprocess.Popen", return_value=Mock(pid=123)) as popen, \
                 patch("model_studio.integrations.opencode._web_url",
                       return_value="http://127.0.0.1:65432"), \
                 patch("model_studio.integrations.opencode.webbrowser.open", return_value=True) as browser:
                result = launch(folder, dict(status="ready", backend="ollama", model_id="test",
                                             context=32768, host="http://127.0.0.1:11434"))
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [str(Path("C:/opencode.exe")), "serve", "--hostname",
                                        "127.0.0.1", "--port", "0"])
            self.assertEqual(kwargs["cwd"], Path(folder).resolve())
            self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(result["url"], "http://127.0.0.1:65432")
            self.assertTrue(kwargs["env"]["OPENCODE_PASSWORD"])
            opened = browser.call_args.args[0]
            self.assertTrue(opened.startswith("http://127.0.0.1:65432/connect#"))
            code = opened.split("#", 1)[1]
            pairing = json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))
            self.assertEqual(pairing, {"username": "opencode",
                                       "password": kwargs["env"]["OPENCODE_PASSWORD"]})
            self.assertNotIn(kwargs["env"]["OPENCODE_PASSWORD"], result["url"])
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")
            self.assertNotIn("OPENCODE_CONFIG_CONTENT", kwargs["env"])
            self.assertNotIn("OPENCODE_CONFIG_DIR", kwargs["env"])
            config_root = Path(kwargs["env"]["XDG_CONFIG_HOME"])
            self.assertTrue(config_root.is_relative_to(Path(data)))
            config = json.loads((config_root / "opencode" / "opencode.json").read_text(encoding="utf-8"))
            self.assertIn("-opencode.browser", config["plugins"])
            self.assertEqual(config["model"], {"providerID": "ollama", "model": "test"})
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_host_failure_disables_plugin_without_stopping_model(self):
        from model_studio.integrations import opencode
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {"MODEL_STUDIO_DATA_DIR": folder}), \
             patch.object(opencode, "executable", return_value=Path("C:/opencode.exe")), \
             patch.object(opencode, "_major_version", return_value=2), \
             patch.object(opencode.subprocess, "run", return_value=Mock(returncode=0, stdout="--hostname --port")), \
             patch.object(opencode.subprocess, "Popen") as popen, \
             patch.object(opencode, "_web_url", return_value="http://127.0.0.1:65432"), \
             patch.object(opencode.webbrowser, "open", return_value=True), \
             patch.object(opencode.browser_host, "available", return_value=True), \
             patch.object(opencode.browser_host, "BrowserHost") as host, \
             patch.object(opencode, "_disable_browser") as disable:
            popen.return_value.pid = 123
            popen.return_value.poll.return_value = None
            host.return_value.start.side_effect = RuntimeError("not attached")
            result = launch(folder, dict(status="ready", model="test", context=32768,
                                        host="http://127.0.0.1:8080"))
            self.assertEqual(result["browser"]["status"], "unavailable")
            disable.assert_called_once()
            popen.return_value.terminate.assert_not_called()
            opencode.stop_web()
            host.return_value.close.assert_called_once()

    def test_v1_launch_keeps_inline_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("model_studio.integrations.opencode.executable", return_value=Path("C:/opencode.exe")), \
                 patch("model_studio.integrations.opencode.subprocess.run",
                       side_effect=[Mock(returncode=0, stdout="opencode 1.9.0"),
                                    Mock(returncode=0, stdout="OpenCode")]), \
                 patch("model_studio.integrations.opencode.subprocess.Popen", return_value=Mock(pid=321)) as popen:
                launch(folder, dict(status="ready", model="test", context=32768,
                                    host="http://127.0.0.1:8080"), mode="tui")
            args, kwargs = popen.call_args
            self.assertNotIn("--standalone", args[0])
            config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual(config["model"], "studio-local/test")
            self.assertIn("provider", config)

    def test_web_launch_failure_terminates_only_owned_child(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch("model_studio.integrations.opencode.executable", return_value=Path("C:/opencode.exe")), \
             patch("model_studio.integrations.opencode.subprocess.run",
                   side_effect=[Mock(returncode=0, stdout="opencode v2.0.15"),
                                Mock(returncode=0, stdout="--hostname --port")]), \
             patch("model_studio.integrations.opencode.subprocess.Popen") as popen, \
             patch("model_studio.integrations.opencode._web_url",
                   side_effect=RuntimeError("web failed")):
            popen.return_value.poll.return_value = None
            with self.assertRaisesRegex(RuntimeError, "web failed"):
                launch(folder, dict(status="ready", model="test", context=32768,
                                    host="http://127.0.0.1:8080"))
        popen.return_value.terminate.assert_called_once()
        popen.return_value.wait.assert_called_once_with(timeout=5)

    def test_repeated_web_launch_reuses_owned_process_and_stop_is_scoped(self):
        from model_studio.integrations import opencode
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(opencode, "_owned_web", None), \
             patch("model_studio.integrations.opencode.executable", return_value=Path("C:/opencode.exe")), \
             patch("model_studio.integrations.opencode.subprocess.run",
                   side_effect=[Mock(returncode=0, stdout="opencode v2.0.15"),
                                Mock(returncode=0, stdout="--hostname --port"),
                                Mock(returncode=0, stdout="opencode v2.0.15")]), \
             patch("model_studio.integrations.opencode.subprocess.Popen") as popen, \
             patch("model_studio.integrations.opencode._web_url",
                   return_value="http://127.0.0.1:65432"), \
             patch("model_studio.integrations.opencode.webbrowser.open", return_value=True) as browser:
            popen.return_value.pid = 123
            popen.return_value.poll.return_value = None
            snapshot = dict(status="ready", model="test", context=32768,
                            host="http://127.0.0.1:8080", session_id="s1")
            first = launch(folder, snapshot)
            second = launch(folder, snapshot)
            self.assertEqual(first, second)
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(browser.call_count, 2)
            opencode.stop_web()
            popen.return_value.terminate.assert_called_once()
            popen.return_value.wait.assert_called_once_with(timeout=5)

    @unittest.skipUnless(os.environ.get("MODEL_STUDIO_LIVE_OPENCODE") == "1",
                         "requires installed OpenCode and running Ollama")
    def test_installed_v2_private_server_registers_both_provider_modes(self):
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            tag = json.load(response)["models"][0]["name"]
        for backend, provider in (("ollama", "ollama"), ("llama.cpp", "studio-local")):
            with self.subTest(backend=backend):
                self._assert_private_provider(tag, backend, provider)

    def _assert_private_provider(self, tag, backend, provider):
        with tempfile.TemporaryDirectory(prefix="studio-opencode-live-") as root:
            config_dir = Path(root) / "opencode"
            config_dir.mkdir()
            config = connection_config(dict(status="ready", backend=backend, model_id=tag,
                                            host="http://127.0.0.1:11434", context=32768))
            (config_dir / "opencode.json").write_text(json.dumps(config), encoding="utf-8")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = os.environ.copy()
            password = secrets.token_urlsafe(24)
            env.update(XDG_CONFIG_HOME=root, OPENCODE_DISABLE_PROJECT_CONFIG="1",
                       OPENCODE_PASSWORD=password)
            proc = subprocess.Popen([str(executable()), "serve", "--hostname", "127.0.0.1",
                                     "--port", str(port)], cwd=root, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                models = []
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    try:
                        token = base64.b64encode(f"opencode:{password}".encode()).decode()
                        request = Request(f"http://127.0.0.1:{port}/api/model",
                                          headers={"Authorization": "Basic " + token})
                        with urlopen(request, timeout=2) as response:
                            models = json.load(response).get("data") or []
                    except OSError:
                        pass
                    if any(row.get("id") == tag and row.get("providerID") == provider
                           for row in models):
                        break
                    time.sleep(0.3)
                self.assertTrue(any(row.get("id") == tag and row.get("providerID") == provider
                                    for row in models), models[:2])
            finally:
                proc.terminate()
                proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
