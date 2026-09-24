"""Qt presentation contract without a real model server or GUI window."""

from pathlib import Path
import os
from queue import SimpleQueue
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from PySide6.QtWidgets import QApplication
    from model_studio.desktop.controllers import Studio
except ImportError:
    QApplication = None
    Studio = None

from model_studio.platform.paths import ensure_data_dirs
from model_studio.storage import Store


class ManualWorkers:
    def __init__(self):
        self.events = SimpleQueue()
        self.jobs = {}

    def submit(self, name, function, session=False):
        self.jobs[name] = function

    def complete(self, name):
        function = self.jobs.pop(name)
        try:
            self.events.put(("done", {"name": name, "value": function()}))
        except Exception as exc:
            self.events.put(("failed", {"name": name, "message": str(exc)}))

    def shutdown(self):
        pass


class FakeCore:
    def __init__(self):
        self.snapshot = {"status": "stopped", "config": None, "session_id": None,
                         "context": None, "model_id": None}
        self.client = Mock(backend="ollama", host="http://127.0.0.1:11434")
        self.started = []
        self.cancelled = False
        self.measurement = {}

    def start(self, config):
        self.started.append(config)
        self.snapshot = {"status": "ready", "config": config.to_dict(), "session_id": "session-1",
                         "context": config.context, "model_id": config.model, "model": config.model}
        return self.snapshot

    def benchmark(self):
        return dict(self.measurement)

    def cancel(self):
        self.cancelled = True

    def unload(self):
        self.snapshot = {"status": "stopped", "config": None, "session_id": None}
        return self.snapshot


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class StudioDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = ensure_data_dirs(self.root / "data")
        self.store = Store(self.paths["database"])
        self.studio = Studio(self.store, self.paths, self.root, initialize=False)
        self.studio._timer.stop()
        self.studio.workers.shutdown()
        self.workers = ManualWorkers()
        self.studio.workers = self.workers
        self.core = FakeCore()
        self.studio.core = self.core
        self.studio.chat_service = Mock()
        self.studio._last_telemetry = time.monotonic()
        self.addCleanup(self.studio._timer.stop)
        self.addCleanup(self.studio.deleteLater)

    def _model(self):
        row = self.store.upsert_model({"backend": "ollama", "locator": "host/tag",
                                       "host": "http://127.0.0.1:11434", "tag": "tag",
                                       "name": "tag", "digest": "sha256:abc",
                                       "identity_verified": True})
        self.studio._values["models"] = [row]
        self.studio._values["selectedModel"] = row
        self.studio._values["settings"] = {"backend_hosts": {"Ollama": "http://127.0.0.1:11434"}}
        return row

    def test_stale_conversation_callbacks_cannot_overwrite_new_or_newer_selection(self):
        first = self.store.create_conversation("first")
        second = self.store.create_conversation("second")
        self.store.save_message(first["id"], "user", "first message")
        self.store.save_message(second["id"], "user", "second message")

        self.studio.selectConversation(first["id"])
        old_job = next(iter(self.workers.jobs))
        self.studio.newChat()
        self.workers.complete(old_job)
        self.studio._pump()
        self.assertEqual(self.studio.messages, [])
        self.assertIsNone(self.studio._conversation_id)

        self.studio.selectConversation(first["id"])
        first_job = next(iter(self.workers.jobs))
        self.studio.selectConversation(second["id"])
        second_job = next(name for name in self.workers.jobs if name != first_job)
        self.workers.complete(second_job)
        self.studio._pump()
        self.assertEqual(self.studio.messages[0]["text"], "second message")
        self.workers.complete(first_job)
        self.studio._pump()
        self.assertEqual(self.studio.messages[0]["text"], "second message")

    def test_draft_change_does_not_rewrite_running_session(self):
        self._model()
        config = self.studio._config().to_dict()
        self.studio._values["session"] = {"status": "ready", "config": config,
                                           "context": config["context"], "session_id": "session-1"}
        notices = []
        self.studio.confirmationRequested.connect(notices.append)
        self.studio.setDraft("context", 65536)
        self.assertEqual(self.studio.session["config"]["context"], 32768)
        self.assertEqual(self.studio.draft["context"], 65536)
        self.studio.startModel()
        self.assertEqual(len(notices), 1)
        self.assertNotIn("start", self.workers.jobs)

    def test_quick_benchmark_is_saved_before_history_callback(self):
        model = self._model()
        self.core.measurement = {"status": "completed", "server_version": "ollama-1",
            "placement_after_warmup": {"context_length": 32768,
                                       "memory": {"vram_bytes": 1024}},
            "requested_runs": 1, "requested_tokens_per_run": 512,
            "runs": [{"index": 1, "tokens": 512, "generation_seconds": 20.48,
                      "tokens_per_second": 25.0}],
            "summary": {"median_tokens_per_second": 25.0}, "warnings": []}
        env = {"backend": "ollama", "runtime_build": "ollama-1", "hardware": "GPU-A",
               "driver": "D1", "verified": True}
        with patch("model_studio.benchmarks.research.environment_snapshot", return_value=env):
            self.studio.runBenchmark()
            self.assertIn("benchmark", self.workers.jobs)
            self.workers.complete("benchmark")
        self.assertEqual(len(self.store.results(model["id"])), 1)
        self.studio._pump()
        self.assertEqual(self.studio.selectedResult["speed"], 25.0)
        self.assertEqual(self.studio.selectedResult["model_id"], model["id"])
        self.assertIn("history", self.workers.jobs)
        self.workers.complete("history")
        self.studio._pump()
        self.assertEqual(self.studio.results[0]["model_id"], model["id"])

    def test_settings_aliases_are_normalized_before_persistence(self):
        self.studio._values["settings"] = {"backend_hosts": {"Ollama": "old", "llama.cpp": "old"}}
        self.studio.saveSettings({"ollama_host": "http://127.0.0.1:11435",
                                  "llama_host": "http://127.0.0.1:8082",
                                  "opencode_path": "C:/tools/opencode.exe",
                                  "reports_dir": "ignored"})
        self.workers.complete("settings")
        self.studio._pump()
        saved = self.store.settings()
        self.assertEqual(saved["backend_hosts"]["Ollama"], "http://127.0.0.1:11435")
        self.assertEqual(saved["managed_host"], "http://127.0.0.1:8082")
        self.assertEqual(saved["opencode_exe"], "C:/tools/opencode.exe")
        for alias in ("ollama_host", "llama_host", "opencode_path", "reports_dir"):
            self.assertNotIn(alias, saved)

    def test_restore_is_rejected_with_a_ready_session(self):
        self.studio._values["session"] = {"status": "ready", "session_id": "session-1"}
        with patch("model_studio.desktop.controllers.QFileDialog.getOpenFileName") as choose:
            self.studio.restoreBackup()
        choose.assert_not_called()
        self.assertNotIn("restore", self.workers.jobs)
        self.assertIn("выгрузите", self.studio.error)

    def test_auxiliary_gguf_cannot_be_launched(self):
        self._model()
        self.studio._values["selectedModel"]["testable"] = False
        self.studio.startModel()
        self.assertNotIn("start", self.workers.jobs)
        self.assertTrue(self.studio.error)

    def test_native_file_ids_cross_qt_without_integer_overflow(self):
        self.studio._values["models"] = [{"id": "m", "fingerprint": [16723647574826069622, 7206168928]}]
        exposed = self.studio.models[0]["fingerprint"]
        self.assertEqual(exposed, ["16723647574826069622", 7206168928])
        self.assertEqual(self.studio._values["models"][0]["fingerprint"][0], 16723647574826069622)

    def test_device_peak_is_not_confused_with_model_placement(self):
        row = self.studio._result_view({"gpu_peak_bytes": 10 * 2**30,
            "memory": {"vram_bytes": 7 * 2**30}})
        self.assertEqual(row["vram_gb"], 10)
        self.assertEqual(row["model_vram_gb"], 7)
        unknown = self.studio._result_view({"memory": {"vram_bytes": 7 * 2**30}})
        self.assertIsNone(unknown["vram_gb"])

    def test_failed_conversation_load_allows_next_action(self):
        with patch.object(self.store, "messages", side_effect=OSError("disk failure")):
            self.studio.selectConversation("missing")
            self.workers.complete(next(iter(self.workers.jobs)))
        self.studio._pump()
        self.assertFalse(self.studio._loading_conversation)
        self.assertIn("disk failure", self.studio.error)

    def test_opencode_open_uses_serial_worker_and_rejects_changed_session(self):
        self.studio._values["selectedProject"] = {"path": str(self.root)}
        self.studio._values["settings"] = {"opencode_exe": "opencode.exe"}
        self.core.snapshot = {"status": "ready", "session_id": "old", "model_id": "old-model"}
        self.studio._values["session"] = dict(self.core.snapshot)
        with patch("model_studio.desktop.controllers.opencode.launch") as launch:
            self.studio.openOpenCode()
            self.assertTrue(self.studio.busy)
            self.core.snapshot = {"status": "ready", "session_id": "new", "model_id": "new-model"}
            self.workers.complete("opencode")
            self.studio._pump()
        launch.assert_not_called()
        self.assertFalse(self.studio.busy)
        self.assertIn("Сессия изменилась", self.studio.error)
