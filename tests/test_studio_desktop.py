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

    def test_disconnected_sessions_keep_polling_without_telemetry(self):
        self.core.refresh_status = Mock(return_value={"status": "ready"})
        for status in ("disconnected", "context_changed", "model_unloaded"):
            self.studio._values["session"] = {"status": status}
            self.studio._last_telemetry = 0
            self.studio._pump()
            self.assertIn("status_poll", self.workers.jobs)
            self.assertNotIn("telemetry_poll", self.workers.jobs)
            self.workers.complete("status_poll")
            self.studio._pump()
        self.assertEqual(self.core.refresh_status.call_count, 3)

    def _model(self):
        row = self.store.upsert_model({"backend": "ollama", "locator": "host/tag",
                                       "host": "http://127.0.0.1:11434", "tag": "tag",
                                       "name": "tag", "digest": "sha256:abc",
                                       "identity_verified": True})
        self.studio._values["models"] = [row]
        self.studio._values["selectedModel"] = row
        self.studio._values["settings"] = {"backend_hosts": {"Ollama": "http://127.0.0.1:11434"}}
        return row

    def test_rename_updates_labels_without_changing_launch_draft(self):
        model = self._model()
        self.studio._values["session"] = {"status": "ready", "config": {"model_id": model["id"]}}
        original_draft = dict(self.studio.draft)
        self.studio.renameModel(model["id"], "Мой помощник")
        self.workers.complete("rename_model")
        self.studio._pump()
        self.assertEqual(self.studio.selectedModel["name"], "Мой помощник")
        self.assertEqual(self.studio.session["model_name"], "Мой помощник")
        self.assertEqual(self.studio.selectedModel["tag"], "tag")
        self.assertEqual(self.studio.draft, original_draft)
        self.assertEqual(self.core.started, [])

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

    def test_discovered_options_enable_launch_without_losing_saved_evidence(self):
        model = {"id": "gguf", "backend": "gguf", "path": str(self.root / "model.gguf"),
                 "name": "model", "available": True, "identity_verified": True, "digest": "abc"}
        exe = str(self.root / "server.exe")
        self.studio._values.update(models=[model], selectedModel=model,
            settings={"server_exe": exe}, runtimeCapabilities={exe: ["reasoning", "kv-cache"]})
        with patch.object(self.studio, "_capture_environment"):
            self.studio._select(model)
        self.assertIn("reasoning", self.studio.selectedModel["capabilities"])
        self.studio.setDraft("reasoning", "off")
        self.studio.setDraft("kv_type", "q8_0")
        config = self.studio._config()
        self.assertEqual((config.reasoning, config.kv_type), ("off", "q8_0"))
        env = {"verified": True, "backend": "llama.cpp", "runtime_build": "build", "hardware": "gpu", "driver": "driver"}
        self.studio._environment = env
        self.studio._values["results"] = [{"id": "saved", "model_id": "gguf", "status": "completed",
            "config": {**config.to_dict(), "capabilities": [], "runtime_name": "old name"},
            "artifact": {"digest": "abc"}, "environment": env,
            "effective_config_verified": True, "comparison_eligible": True}]
        self.studio._refresh_recommendations()
        self.assertEqual(self.studio.matchingResult["id"], "saved")

    def test_projector_picker_offers_discovered_module_and_cancel_does_not_enable(self):
        model = {"id": "gguf", "backend": "gguf", "path": str(self.root / "model.gguf"), "available": True}
        projector = {"backend": "gguf", "path": str(self.root / "vision-mmproj.gguf"), "available": True, "testable": False}
        self.studio._values.update(selectedModel=model, models=[model, projector])
        with patch("model_studio.desktop.controllers.QFileDialog.getOpenFileName", return_value=("", "")) as picker:
            self.studio.chooseProjector()
            self.assertEqual(picker.call_args.args[2], projector["path"])
            self.assertFalse(self.studio.draft["vision"])
        with patch("model_studio.desktop.controllers.QFileDialog.getOpenFileName", return_value=(projector["path"], "")):
            self.studio.chooseProjector()
        self.assertTrue(self.studio.draft["vision"])
        self.workers.complete("settings")
        self.assertEqual(self.store.settings()["model_projectors"][model["path"]], projector["path"])

    def test_budget_is_atomic_guarded_and_cleared_by_legacy_recommendation(self):
        model = {"id": "gguf", "backend": "gguf", "path": str(self.root / "model.gguf"),
                 "name": "model", "available": True, "identity_verified": True, "digest": "abc"}
        exe = str(self.root / "server.exe")
        self.studio._values.update(models=[model], selectedModel=model, settings={"server_exe": exe},
            runtimeCapabilities={exe: ["reasoning", "reasoning-budget"]})
        with patch.object(self.studio, "_capture_environment"):
            self.studio._select(model)
        old_config = self.studio._config().to_dict()
        old_config.pop("reasoning_budget")
        for budget in (2048, 4096, 8192):
            self.studio.setReasoning("on", budget)
            self.assertEqual(self.studio._config().reasoning_budget, budget)
        self.studio._values.update(results=[{"id": "old", "config": old_config}],
            recommendations=[{"key": "speed", "available": True, "result_id": "old"}])
        self.studio.applyRecommendation("speed")
        self.assertIsNone(self.studio._config().reasoning_budget)
        self.studio.setReasoning("on", 4096)
        self.studio.setReasoning("off", 0)
        self.assertEqual(self.studio._config().reasoning, "off")
        self.assertIsNone(self.studio._config().reasoning_budget)
        self.studio.setReasoning("auto", 0)
        self.studio._values["selectedModel"]["capabilities"] = ["reasoning"]
        self.studio.setReasoning("on", 2048)
        self.assertEqual(self.studio.draft["reasoning"], "auto")
        self.assertIsNone(self.studio.draft["reasoning_budget"])

    def test_research_failure_exposes_original_and_restore_errors(self):
        job = {"id": "failed-job", "status": "failed", "error": "context mismatch",
               "restore_error": "server unavailable", "stop_reason": "error"}
        self.workers.events.put(("research_finished", job))
        self.studio._pump()
        self.assertEqual(self.studio.research["id"], "failed-job")
        self.assertIn("context mismatch", self.studio.error)
        self.assertIn("server unavailable", self.studio.error)

    def test_matching_result_uses_requested_settings_with_verified_effective_context(self):
        model = self._model()
        model.update(backend="gguf", path="selected.gguf")
        self.studio._values["settings"]["server_exe"] = "llama-server.exe"
        self.studio._values["draft"]["context"] = 100000
        config = self.studio._config().to_dict()
        environment = {"backend": "llama.cpp", "runtime_build": "v1", "hardware": "gpu",
                       "driver": "d1", "verified": True}
        self.studio._environment = environment
        result = {"id": "rounded", "model_id": model["id"], "config": config,
                  "effective_config": {**config, "context": config["context"] + 96},
                  "effective_config_verified": True, "comparison_eligible": True,
                  "artifact": {"digest": model["digest"]}, "environment": environment,
                  "status": "completed"}
        self.studio._values["results"] = [result]
        self.studio._refresh_recommendations()
        self.assertEqual(self.studio.matchingResult["id"], "rounded")
        self.studio._values["draft"]["context"] += 1024
        self.studio._refresh_recommendations()
        self.assertEqual(self.studio.matchingResult, {})

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

    def test_legacy_report_can_be_displayed_and_exported_without_verified_identity(self):
        model = self._model()
        model.update(backend="gguf", path=str(self.root / "model.gguf"))
        legacy = {"id": "legacy", "legacy_source": "old.json", "model": model["path"],
                  "context": 32768, "identity_verified": False,
                  "cases": [{"speed": 51.2}]}
        shown = self.studio._result_view(legacy)
        self.assertEqual(shown["display_model_id"], model["id"])
        self.assertEqual(shown["context"], 32768)
        self.assertNotIn("model_id", shown)
        self.assertFalse(shown["identity_verified"])
        self.studio._values["results"] = [shown]
        self.studio.selectResult("legacy")
        with patch.object(self.studio, "copyText") as copy:
            self.studio.copyReport()
        report = copy.call_args.args[0]
        self.assertIn("32768", report)
        self.assertIn("51.2", report)
        self.assertNotIn(str(self.root), report)
        self.studio.exportReport()
        self.workers.complete("export")
        self.studio._pump()
        self.assertEqual(len(list(self.paths["reports"].glob("*.json"))), 1)
        self.assertNotIn("display_model_id", legacy)

    def test_bulk_copy_reads_unloaded_rows_and_respects_model_and_filters(self):
        model = self._model()
        first = self.store.save_result({"model_id": model["id"], "status": "completed",
            "config": {"context": 32768, "backend": "ollama"}})
        second = self.store.save_result({"model_id": model["id"], "status": "completed",
            "config": {"context": 32768, "backend": "ollama"}})
        hidden = self.store.save_result({"model_id": model["id"], "status": "error",
            "config": {"context": 65536, "backend": "ollama"}})
        unrelated = self.store.save_result({"status": "completed", "config": {"context": 32768}})
        self.studio._values["results"] = [first]
        with patch("model_studio.desktop.controllers.QGuiApplication.clipboard") as clipboard:
            self.studio.copyReports({"context": "32768", "status": "completed", "backend": "ollama"})
            self.workers.complete("copy_reports")
            self.studio._pump()
        report = clipboard.return_value.setText.call_args.args[0]
        self.assertIn(first["id"], report)
        self.assertIn(second["id"], report)
        self.assertNotIn(hidden["id"], report)
        self.assertNotIn(unrelated["id"], report)
        self.assertIn("Замеров: 2", self.studio.notice)

    def test_research_copy_keeps_failures_and_open_does_not_start_session(self):
        model = self._model()
        job = self.store.create_research({"base_config": {"model_id": model["id"]}})
        result = self.store.save_result({"model_id": model["id"], "research_id": job["id"],
            "status": "error", "error": "GPU memory exhausted", "config": {"context": 131072}})
        job = self.store.update_research(job["id"], {"status": "stopped", "stop_reason": "fit_failure",
            "completed_steps": [{"result_id": result["id"]}]})
        self.studio._values.update(results=[], researchJobs=[job])
        with patch("model_studio.desktop.controllers.QGuiApplication.clipboard") as clipboard:
            self.studio.copyReports({"research_id": job["id"], "status": "completed"})
            self.workers.complete("copy_reports")
            self.studio._pump()
        report = clipboard.return_value.setText.call_args.args[0]
        self.assertIn("GPU memory exhausted", report)
        self.assertIn("fit_failure", report)
        self.studio.showResearch(job["id"])
        self.workers.complete("research_results:" + job["id"])
        self.studio._pump()
        self.assertEqual(self.studio.researchResults[0]["id"], result["id"])
        self.assertEqual(self.core.started, [])
        self.studio.selectResult(result["id"])
        self.assertEqual(self.studio.selectedResult["id"], result["id"])
        self.studio.showResearch(job["id"])
        self.studio.clearResearchView()
        self.workers.complete("research_results:" + job["id"])
        self.studio._pump()
        self.assertEqual(self.studio.researchResults, [])

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
