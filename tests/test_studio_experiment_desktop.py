"""Research draft isolation and asynchronous presentation contracts."""
from unittest.mock import patch

import unittest
import test_studio_desktop as support


@unittest.skipIf(support.QApplication is None, "PySide6 is not installed")
class ExperimentDesktopTests(unittest.TestCase):
    setUp = support.StudioDesktopTests.setUp
    _model = support.StudioDesktopTests._model

    @classmethod
    def setUpClass(cls):
        support.StudioDesktopTests.setUpClass()

    def test_research_has_own_draft_and_explicit_copy(self):
        self._model()
        self.studio._values["draft"]["context"] = 98304
        self.studio.ensureResearchDraft()
        self.assertEqual(self.studio.researchDraft["contexts"], [32768])
        self.studio.copyLaunchToResearch()
        self.assertEqual(self.studio.researchDraft["contexts"], [98304])
        self.studio.setDraft("context", 65536)
        self.assertEqual(self.studio.researchDraft["contexts"], [98304])
        self.studio.setResearchOption("contexts", [32768, 131072])
        self.assertEqual(self.studio.draft["context"], 65536)

    def test_qml_custom_axes_preserve_integer_types_without_rounding(self):
        from PySide6.QtQml import QJSEngine
        from model_studio.benchmarks.experiment import experiment_candidates
        self._model()
        self.studio.ensureResearchDraft()
        engine = QJSEngine()
        engine.setObjectOwnership(self.studio, QJSEngine.ObjectOwnership.CppOwnership)
        engine.globalObject().setProperty("studio", engine.newQObject(self.studio))
        result = engine.evaluate('studio.setResearchOption("contexts", [Number("98304")]); '
                                 'studio.setResearchOption("mtp_variants", [Number("3")]);')
        self.assertFalse(result.isError(), result.toString())
        draft = self.studio.researchDraft
        self.assertIs(type(draft["contexts"][0]), int)
        self.assertIs(type(draft["mtp_variants"][0]), int)
        self.assertEqual((draft["contexts"], draft["mtp_variants"]), ([98304], [3]))
        engine.evaluate('studio.setResearchOption("contexts", [32768.5]);')
        self.assertEqual(self.studio.researchDraft["contexts"], [32768.5])
        config, plan = self.studio.experiments.plan()
        with self.assertRaisesRegex(ValueError, "контекст"):
            experiment_candidates(config, plan)

    def test_late_preview_cannot_replace_newer_draft(self):
        self._model()
        self.studio.ensureResearchDraft()
        self.studio.experiments.timer.stop()
        with patch("model_studio.benchmarks.experiment.preview_experiment", return_value={"total": 1, "signature": "old"}):
            self.studio.experiments.preview()
            old = next(k for k in self.workers.jobs if k.startswith("experiment_preview:"))
            self.studio.setResearchOption("contexts", [65536, 98304])
            self.studio.experiments.timer.stop()
            self.workers.complete(old)
            self.studio._pump()
        self.assertEqual(self.studio.researchPreview["status"], "pending")
        self.assertNotIn("signature", self.studio.researchPreview)

    def test_model_change_preserves_axes_for_visible_validation(self):
        first = self._model()
        self.studio.ensureResearchDraft()
        self.studio.setResearchOption("mtp_variants", [0, 2])
        second = {**first, "id": "second", "name": "Другая модель"}
        self.studio._values["models"].append(second)
        self.studio.selectResearchModel("second")
        self.assertEqual(self.studio.researchDraft["mtp_variants"], [0, 2])
        self.assertEqual(self.studio.selectedModel["id"], first["id"])

    def test_report_snapshot_does_not_follow_later_selection(self):
        model = self._model()
        self.studio.ensureResearchDraft()
        row = self.store.save_result({"model_id": model["id"], "status": "completed",
            "config": {"context": 32768}, "summary": {"median_scenario_seconds": 42}})
        snapshot = self.studio.experiments.report_snapshot()
        self.studio._values["researchDraft"]["model_id"] = "different"
        text = self.studio.experiments.report("history", snapshot)
        self.assertIn(row["id"], text)

    def test_start_passes_research_snapshot_and_signature(self):
        self._model()
        self.studio.ensureResearchDraft()
        self.studio.experiments.timer.stop()
        self.studio.setDraft("context", 98304)
        self.studio._values["researchPreview"] = {"status": "ready", "signature": "reviewed", "checks_total": 1}
        with patch("model_studio.benchmarks.research.run_research", return_value={"id": "job", "status": "completed"}) as run:
            self.studio.startExperiment()
            self.workers.complete("research")
        args = run.call_args.args
        self.assertEqual(args[2].context, 32768)
        self.assertEqual(args[3]["preview_signature"], "reviewed")
        self.assertEqual(args[3]["contexts"], [32768])

    def test_editing_after_start_confirmation_does_not_launch_stale_plan(self):
        self._model()
        self.studio.ensureResearchDraft()
        self.studio.experiments.timer.stop()
        self.studio._values["session"] = {"status": "ready"}
        self.studio._values["researchPreview"] = {"status": "ready", "signature": "reviewed", "checks_total": 1}
        self.studio.startExperiment()
        self.assertIsNotNone(self.studio._confirmed_operation)
        self.studio.setResearchOption("contexts", [65536])
        self.studio.confirmOperation(True)
        self.assertNotIn("research", self.workers.jobs)
        self.assertIn("Параметры изменились", self.studio.error)

    def test_apply_result_changes_launch_draft_without_starting(self):
        model = self._model()
        config = self.studio._config().to_dict()
        config["context"] = 65536
        self.studio._values["experimentResults"] = [{"id": "row", "model_id": model["id"],
            "status": "completed", "config": config}]
        self.studio.applyExperimentResult("row")
        self.assertEqual(self.studio.draft["context"], 65536)
        self.assertEqual(self.core.started, [])
        self.assertEqual(self.studio.page, 0)

    def _saved_gguf_result(self):
        from agent_fixtures import agent_measurement
        model = self.store.upsert_model({"backend": "gguf", "path": str(self.root / "model.gguf"),
            "name": "model", "available": True, "identity_verified": True, "digest": "digest"})
        exe = str(self.root / "server.exe")
        self.studio._values.update(models=[model], selectedModel=model,
            settings={"server_exe": exe, "runtime_profiles": {
                "saved": {"executable": exe, "extra_args": ["--flash-attn", "off"]}}})
        config = self.studio._config().to_dict()
        config.update(extra_args=["--flash-attn", "off"], context=65536,
                      host="http://127.0.0.1:8123")
        env = {"backend": "llama.cpp", "verified": True, "runtime_build": "v1",
               "hardware": "GPU-A", "driver": "D1"}
        result = self.store.save_result({**agent_measurement(), "model_id": model["id"],
            "config": config, "effective_config": config, "effective_config_verified": True,
            "comparison_eligible": True, "artifact": {"identity_verified": True, "digest": "digest"},
            "environment": env})
        self.studio._environment = env
        return model, result

    def test_recommendation_loads_full_model_history_and_applies_saved_runtime(self):
        model, result = self._saved_gguf_result()
        for index in range(200):
            self.store.save_result({"id": f"unrelated-{index}", "status": "error",
                                    "created_at": "2099-01-01"})
        self.studio._values["results"] = self.store.results(limit=200)
        self.assertNotIn(result["id"], [r["id"] for r in self.studio.results])
        self.studio._load_recommendation_results()
        self.workers.complete("recommendation_history:" + str(self.studio._recommendation_revision))
        self.studio._pump()
        self.assertEqual(self.studio.recommendations[0]["result_id"], result["id"])
        self.assertEqual(len(self.studio.results), 200)
        self.studio.applyRecommendation("speed")
        self.workers.complete("apply_saved_result")
        self.studio._pump()
        applied = self.studio._config().to_dict()
        for key in ("executable", "extra_args", "context", "host", "mmproj", "reasoning_budget"):
            self.assertEqual(applied[key], result["config"][key])
        self.assertEqual(self.store.settings()["model_profiles"][model["path"]], "saved")
        self.assertEqual(self.core.started, [])

    def test_research_and_recommendation_share_runtime_application(self):
        model, result = self._saved_gguf_result()
        self.studio._values["experimentResults"] = [result]
        self.studio.applyExperimentResult(result["id"])
        self.workers.complete("apply_saved_result")
        self.studio._pump()
        self.assertEqual(list(self.studio._config().extra_args), result["config"]["extra_args"])
        self.assertEqual(self.core.started, [])

    def test_unavailable_recommendation_profile_leaves_draft_and_settings_intact(self):
        model, result = self._saved_gguf_result()
        self.studio._values["settings"]["runtime_profiles"] = {}
        self.studio._values.update(results=[result], recommendations=[
            {"key": "speed", "available": True, "result_id": result["id"]}])
        before = self.studio._config().to_dict()
        self.studio.applyRecommendation("speed")
        self.assertNotIn("apply_saved_result", self.workers.jobs)
        self.assertEqual(self.studio._config().to_dict(), before)
        self.assertIn("больше не настроена", self.studio.error)

    def test_old_model_history_callback_cannot_replace_newer_selection(self):
        first = self._model()
        second = self.store.upsert_model({**first, "id": "second", "tag": "second"})
        self.studio._values["models"].append(second)
        self.studio._load_recommendation_results()
        old_job = "recommendation_history:" + str(self.studio._recommendation_revision)
        self.studio._values["selectedModel"] = second
        self.studio._load_recommendation_results()
        new_job = "recommendation_history:" + str(self.studio._recommendation_revision)
        self.workers.complete(new_job)
        self.workers.complete(old_job)
        self.studio._pump()
        self.assertEqual(self.studio._recommendation_model_id, second["id"])

    def test_study_projection_joins_proofs_and_keeps_partial_saved_work(self):
        model = self._model()
        self.studio.ensureResearchDraft()
        self.studio.experiments.timer.stop()
        config = self.studio._config().to_dict()
        job = self.store.create_research({"scope": "experiment", "base_config": config})
        speed = self.store.save_result({"model_id": model["id"], "config": config,
            "created_at": "2026-09-20", "status": "completed", "summary": {"median_scenario_seconds": 42}})
        long = self.store.save_result({"model_id": model["id"], "research_id": job["id"], "config": config,
            "created_at": "2026-09-26", "status": "completed", "long_context": {"validated": True}})
        partial = self.store.save_result({"model_id": model["id"], "research_id": job["id"],
            "config": {**config, "context": 65536}, "status": "completed", "long_context": {"validated": True}})
        self.store.update_research(job["id"], {"completed_steps": [
            {"key": "one", "status": "error", "config": config},
            {"key": "one", "status": "completed", "speed_result_id": speed["id"],
             "long_result_id": long["id"], "speed_status": "history", "long_status": "measure", "config": config}]})
        self.studio.setExperimentScope(job["id"])
        name = "experiment_history:" + str(self.studio.experiments.history_revision)
        self.workers.complete(name)
        self.studio._pump()
        rows = self.studio.experimentResults
        self.assertEqual(len(rows), 2)
        combined = next(r for r in rows if r["id"] == speed["id"])
        self.assertEqual(combined["scenario_seconds"], 42)
        self.assertEqual(combined["speed_created_at"], "2026-09-20")
        self.assertEqual(combined["long_created_at"], "2026-09-26")
        self.assertIn(partial["id"], [r["id"] for r in rows])
