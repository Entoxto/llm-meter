import base64
import json
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
from model_studio.integrations.opencode import connection_config, executable, launch


class OpenCodeTests(unittest.TestCase):
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
                                    Mock(returncode=0, stdout="--standalone")]), \
                 patch("model_studio.integrations.opencode.subprocess.Popen", return_value=Mock(pid=123)) as popen:
                launch(folder, dict(status="ready", backend="ollama", model_id="test",
                                    context=32768, host="http://127.0.0.1:11434"))
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [str(Path("C:/opencode.exe")), "--standalone",
                                        str(Path(folder).resolve())])
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")
            self.assertNotIn("OPENCODE_CONFIG_CONTENT", kwargs["env"])
            self.assertNotIn("OPENCODE_CONFIG_DIR", kwargs["env"])
            config_root = Path(kwargs["env"]["XDG_CONFIG_HOME"])
            self.assertTrue(config_root.is_relative_to(Path(data)))
            config = json.loads((config_root / "opencode" / "opencode.json").read_text(encoding="utf-8"))
            self.assertEqual(config["model"], {"providerID": "ollama", "model": "test"})
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_v1_launch_keeps_inline_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("model_studio.integrations.opencode.executable", return_value=Path("C:/opencode.exe")), \
                 patch("model_studio.integrations.opencode.subprocess.run",
                       side_effect=[Mock(returncode=0, stdout="opencode 1.9.0"),
                                    Mock(returncode=0, stdout="OpenCode")]), \
                 patch("model_studio.integrations.opencode.subprocess.Popen", return_value=Mock(pid=321)) as popen:
                launch(folder, dict(status="ready", model="test", context=32768,
                                    host="http://127.0.0.1:8080"))
            args, kwargs = popen.call_args
            self.assertNotIn("--standalone", args[0])
            config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual(config["model"], "studio-local/test")
            self.assertIn("provider", config)

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
