"""Companion lifecycle and credential boundary, without starting Chromium."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_studio.integrations.browser_host import BrowserHost


class BrowserHostTests(unittest.TestCase):
    def setUp(self):
        stale = patch.object(BrowserHost, "_revoke_stale")
        stale.start()
        self.addCleanup(stale.stop)

    def test_credentials_use_stdin_and_never_process_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "ModelStudioBrowser.exe").touch()
            process = Mock(stdin=io.StringIO(), pid=456)
            process.poll.return_value = None
            with patch("model_studio.integrations.browser_host.bundle_root", return_value=root), \
                 patch("model_studio.integrations.browser_host.subprocess.Popen", return_value=process) as popen, \
                 patch("model_studio.integrations.browser_host.threading.Thread"), \
                 patch.object(BrowserHost, "snapshot", return_value={"status": "ready"}):
                host = BrowserHost()
                host.start("http://127.0.0.1:43123", "private-password", "C:/Project")
                payload = json.loads(process.stdin.getvalue())
                self.assertEqual(payload["password"], "private-password")
                self.assertEqual(payload["project"], "C:/Project")
                self.assertNotIn("private-password", str(popen.call_args))
                self.assertNotIn("ELECTRON_RUN_AS_NODE", popen.call_args.kwargs["env"])
                host.close()
                self.assertTrue(process.stdin.closed)
                process.wait.assert_called_once_with(timeout=5)

    def test_start_timeout_cleans_up_only_created_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "ModelStudioBrowser.exe").touch()
            process = Mock(stdin=io.StringIO())
            process.poll.return_value = None
            process.wait.side_effect = [subprocess.TimeoutExpired("host", 5), 0]
            with patch("model_studio.integrations.browser_host.bundle_root", return_value=root), \
                 patch("model_studio.integrations.browser_host.subprocess.Popen", return_value=process), \
                 patch("model_studio.integrations.browser_host.threading.Thread"):
                host = BrowserHost()
                with self.assertRaisesRegex(RuntimeError, "подтвердил"):
                    host.start("http://127.0.0.1:43123", "secret", "C:/Project", timeout=0)
                process.terminate.assert_called_once()
                self.assertIsNone(host.process)

    def test_child_messages_cannot_leak_secrets_to_status(self):
        host = BrowserHost()
        host.process = Mock(stdout=io.StringIO(json.dumps({"type": "error", "message": "secret credential"}) + "\n"))
        host._read()
        self.assertEqual(host.snapshot()["status"], "unavailable")
        self.assertNotIn("secret", str(host.snapshot()))

    def test_attachment_grants_only_matching_project_and_detach_revokes(self):
        host = BrowserHost()
        host._project = "C:/Project"
        session = {"id": "ses_one", "location": {"directory": "C:/Project"},
                   "permissions": [{"action": "edit", "resource": "*", "effect": "ask"}]}
        with patch.object(host, "_request", side_effect=[{"data": session}, None]) as request:
            host._set_permission("ses_one", True)
            update = request.call_args.args[1]
            self.assertEqual(update["permissions"][0]["action"], "browser")
            self.assertEqual(update["permissions"][1]["action"], "edit")
        with patch.object(host, "_request", side_effect=[{"data": {**session, **update}}, None]) as request:
            host._set_permission("ses_one", False)
            self.assertEqual(request.call_args.args[1]["permissions"], session["permissions"])
        with patch.object(host, "_request", return_value={**session, "location": {"directory": "C:/Other"}}) as request:
            host._set_permission("ses_one", True)
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
