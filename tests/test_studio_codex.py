"""Public active-session contract; no GPU or running model required."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_studio.integrations.codex import publish_session
from model_studio.session import SessionController


class CodexSessionTests(unittest.TestCase):
    def test_exports_only_connection_fields_and_replaces_old_state(self):
        with tempfile.TemporaryDirectory() as root:
            snapshot = dict(status="ready", busy="idle", backend="ollama",
                            host="http://127.0.0.1:11434", model_id="actual-api-tag",
                            session_id="one", context=8192, config={"secret": "never-export"},
                            model="draft-not-the-api-tag", access_token="never-export")
            target = publish_session(root, snapshot)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["model"], "actual-api-tag")
            self.assertEqual(payload["owner_pid"], os.getpid())
            self.assertEqual(payload["context"], 8192)
            self.assertNotIn("never-export", target.read_text(encoding="utf-8"))
            publish_session(root, dict(snapshot, status="stopped", model_id=None))
            self.assertEqual(json.loads(target.read_text())["status"], "stopped")
            self.assertFalse(list(Path(root).glob(".codex-session-*")))

    def test_session_state_publishes_runtime_identity_and_invalidates_stop(self):
        with tempfile.TemporaryDirectory() as root:
            session = SessionController(root, lambda *args: None)
            session._status, session._busy = "ready", "idle"
            session._session_id, session._model_id = "active", "launched-tag"
            session._state()
            path = Path(root) / "codex-session.json"
            self.assertEqual(json.loads(path.read_text())["model"], "launched-tag")
            session._status, session._model_id = "stopped", None
            session._state()
            self.assertEqual(json.loads(path.read_text())["status"], "stopped")

    def test_export_failure_does_not_break_runtime_state_delivery(self):
        events = []
        with tempfile.TemporaryDirectory() as root:
            session = SessionController(root, lambda event, payload: events.append(event))
            with patch("model_studio.session.publish_session", side_effect=OSError("read-only")):
                session._state()
        self.assertEqual(events, ["error", "session"])
