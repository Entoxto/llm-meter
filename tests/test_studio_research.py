from contextlib import contextmanager
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_studio.benchmarks import (plan_candidates, prepare_result, resume_research,
                                     run_research, recommendations,
                                     report_text, export_result)
from model_studio.configuration import LaunchConfig
from model_studio.storage import Store


class FakeClient:
    backend = "ollama"
    host = "http://127.0.0.1:11434"
    model_info = {"context_source": "fake resident"}

    def __init__(self):
        self.context = 0

    def resident(self, model):
        return {"context_length": self.context,
                "memory": {"vram_bytes": 1000, "ram_estimate_bytes": 0}}


class FakeSession:
    def __init__(self, initial=None):
        self.client = FakeClient()
        self.model_id = "runtime-name"
        self._stop = threading.Event()
        self.started = []
        self.unloads = 0
        self.after_benchmark = None
        self.snapshot = initial or {"status": "stopped", "config": None}

    @property
    def cancel_event(self):
        return self._stop

    @contextmanager
    def research_operation(self):
        yield self

    def start(self, config):
        if self._stop.is_set():
            return {"status": "stopped"}
        self.started.append(config)
        self.client.context = config.context
        self.snapshot = {"status": "ready", "config": config.to_dict()}
        return self.snapshot

    def benchmark(self, runs, tokens):
        result = {"status": "completed", "session_id": "session", "runtime_model_id": self.model_id,
                  "runs": [{"index": i, "tokens": tokens, "generation_seconds": 20.48,
                             "tokens_per_second": 25.0}
                           for i in range(1, runs + 1)],
                  "summary": {"median_tokens_per_second": 25.0}, "warnings": []}
        if self.after_benchmark:
            self.after_benchmark()
        return result

    def cancel(self):
        self._stop.set()

    def begin_restoration(self):
        self._stop = threading.Event()
        return self._stop

    def unload(self):
        self.unloads += 1
        self.snapshot = {"status": "stopped", "config": None}
        return self.snapshot


class ResearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root / "studio.db")
        model = self.store.upsert_model({"backend": "ollama", "locator": "host/tag", "name": "tag",
                                         "digest": "sha256:abc", "identity_verified": True})
        self.config = LaunchConfig(model="tag", backend="ollama", managed=False,
                                   host="http://127.0.0.1:11434", model_id=model["id"])
        self.plan = {"contexts": [2048, 4096], "max_configs": 2, "budget_minutes": 1,
                     "runs": 2, "target_context": 4096, "acknowledged_external": True,
                     "environment": {"verified": True, "runtime_build": "v1",
                                     "hardware": "GPU-A", "driver": "D1"}}

    def test_completed_research_persists_each_result_and_unloads(self):
        session = FakeSession()
        job = run_research(session, self.store, self.config, self.plan)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(len(job["completed_steps"]), 2)
        self.assertEqual(session.unloads, 1)
        self.assertEqual(len(self.store.results(self.config.model_id)), 2)
        row = self.store.results()[0]
        self.assertTrue(row["artifact"]["identity_verified"])
        self.assertFalse(row["long_context"]["validated"])
        self.assertEqual(len(row["runs"]), 2)
        self.assertNotIn("prompt", row)

    def test_cancel_after_first_result_keeps_result_and_restores_initial(self):
        original = self.config.to_dict()
        initial = {"status": "ready", "config": original}
        session = FakeSession(initial)
        session.after_benchmark = session.cancel
        job = run_research(session, self.store, self.config, self.plan)
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(len(job["completed_steps"]), 1)
        self.assertEqual(len(self.store.results()), 1)
        self.assertEqual(session.started[-1].context, self.config.context)
        self.assertEqual(session.unloads, 0)

    def test_plan_limit_and_external_acknowledgment(self):
        with self.assertRaises(ValueError):
            run_research(FakeSession(), self.store, self.config,
                         {**self.plan, "max_configs": 13})
        with self.assertRaises(ValueError):
            run_research(FakeSession(), self.store, self.config,
                         {**self.plan, "acknowledged_external": False})
        self.assertEqual(self.store.research_jobs(), [])

    def test_pre_cancel_and_restoration_failure_are_recorded(self):
        cancelled = threading.Event(); cancelled.set()
        first = FakeSession()
        job = run_research(first, self.store, self.config, self.plan, cancel=cancelled)
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["completed_steps"], [])
        self.assertEqual(first.unloads, 1)

        class RestoreFailure(FakeSession):
            def start(self, config):
                if getattr(self, "restoring", False):
                    raise RuntimeError("restore failed")
                return super().start(config)

            def begin_restoration(self):
                self.restoring = True
                return super().begin_restoration()

        initial = {"status": "ready", "config": self.config.to_dict()}
        session = RestoreFailure(initial)
        job = run_research(session, self.store, self.config, self.plan)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["restore_error"], "restore failed")
        self.assertEqual(len(job["completed_steps"]), 2)

    def test_planner_stages_are_bounded_by_capabilities_and_config_limit(self):
        config = LaunchConfig(model="test.gguf", backend="llama.cpp", managed=True,
                              executable="llama-server.exe", model_id="catalog-id",
                              capabilities=("mtp", "kv-cache"), context=32768)
        plan = {"contexts": [32768, 65536, 131072], "max_configs": 8,
                "budget_minutes": 1, "runs": 2, "target_context": 131072,
                "acknowledged_external": True, "max_draft": 4,
                "memory_economy": True}
        planned = plan_candidates(config, plan)
        self.assertEqual(planned[0]["stage"], "baseline")
        self.assertLessEqual(len(planned), 8)
        self.assertTrue(any(c["stage"] == "acceleration" and c["config"].mtp for c in planned))
        self.assertTrue(any(c["stage"] == "context" and c["config"].context == 131072 for c in planned))
        self.assertTrue(any(c["stage"] == "kv" for c in planned))
        self.assertFalse(any(c["stage"] == "kv" for c in plan_candidates(
            config, {**plan, "memory_economy": False})))
        unsupported = LaunchConfig(model="test.gguf", backend="llama.cpp", managed=True,
                                   executable="llama-server.exe", context=32768)
        self.assertFalse(any(c["stage"] in ("acceleration", "kv") for c in plan_candidates(unsupported, plan)))

    def test_explicit_resume_skips_complete_step_and_retries_remaining(self):
        verified_env = {"backend": "ollama", "runtime_build": "v1", "hardware": "GPU-A",
                        "driver": "D1", "verified": True}
        session = FakeSession()
        session.after_benchmark = session.cancel
        with patch("model_studio.benchmarks.research.environment_snapshot", return_value=verified_env):
            first = run_research(session, self.store, self.config, self.plan)
            self.assertEqual(first["status"], "cancelled")
            first_id = first["completed_steps"][0]["result_id"]
            session.after_benchmark = None
            resumed = resume_research(session, self.store, first["id"])
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["id"], first["id"])
        self.assertEqual(len(resumed["completed_steps"]), 2)
        self.assertEqual(resumed["completed_steps"][0]["result_id"], first_id)
        self.assertEqual(len(self.store.results(self.config.model_id)), 2)

    def test_research_uses_best_verified_acceleration_for_context(self):
        class SpeedSession(FakeSession):
            def benchmark(self, runs, tokens):
                on = self.snapshot["config"]["mtp"]
                speed = 35.0 if on else 20.0
                return {"status": "completed", "session_id": "session", "runtime_model_id": self.model_id,
                        "requested_runs": runs, "requested_tokens_per_run": tokens,
                        "runs": [{"index": i, "tokens": tokens, "generation_seconds": tokens / speed,
                                  "tokens_per_second": speed,
                                  "draft_n": 10 if on else None} for i in range(1, runs + 1)],
                        "summary": {"median_tokens_per_second": speed}, "warnings": []}

        model = self.store.upsert_model({"backend": "gguf", "locator": "test.gguf",
                                         "name": "test", "digest": "abc",
                                         "identity_verified": True})
        config = LaunchConfig(model="test.gguf", backend="llama.cpp", managed=True,
                              executable="llama-server.exe", model_id=model["id"],
                              capabilities=("mtp",), context=32768)
        plan = {"contexts": [32768, 65536], "max_configs": 4, "max_draft": 2,
                "budget_minutes": 1, "runs": 2, "target_context": 65536,
                "acknowledged_external": True,
                "environment": {"verified": True, "runtime_build": "v1",
                                "hardware": "GPU-A", "driver": "D1"}}
        session = SpeedSession()
        session.client.backend = "llama.cpp"
        with patch("model_studio.benchmarks.research._long_context_probe",
                   return_value={"validated": False, "reason": "unsupported"}) as probe:
            job = run_research(session, self.store, config, plan)
        self.assertEqual(job["status"], "completed")
        context_step = next(s for s in job["completed_steps"] if s["stage"] == "context")
        self.assertTrue(context_step["config"]["mtp"])
        self.assertEqual(context_step["config"]["context"], 65536)
        self.assertEqual(probe.call_count, 1)  # no huge prompt for each MTP toggle

    def test_quick_result_evidence_can_yield_speed_recommendation(self):
        measured = {"status": "completed", "model_id": self.config.model_id,
                    "runtime_model_id": "tag", "server_version": "ollama-1",
                    "placement_after_warmup": {"context_length": 32768,
                                               "memory": {"vram_bytes": 1000}},
                    "gpu_summary": [{"name": "GPU-A"}],
                    "requested_runs": 3, "requested_tokens_per_run": 512,
                    "options": {"temperature": 0, "seed": 42},
                    "runs": [{"index": i, "tokens": 512, "generation_seconds": 20.48,
                              "tokens_per_second": 25.0}
                             for i in range(1, 4)],
                    "summary": {"median_tokens_per_second": 25.0}}
        with patch("model_studio.benchmarks.research._gpu_signature", return_value=("GPU-A:1000MiB", "D1")):
            snapshot = prepare_result(self.store, self.config, measured)
        self.assertTrue(snapshot["environment"]["verified"])
        self.assertTrue(snapshot["effective_config_verified"])
        self.assertTrue(snapshot["comparison_eligible"])
        cards = recommendations([snapshot], self.config.to_dict(),
                                current_environment=snapshot["environment"])
        self.assertTrue(cards[0]["available"])
        self.assertTrue(cards[0]["exact_current"])
        self.assertFalse(cards[1]["available"])
        self.assertFalse(recommendations([snapshot], self.config.to_dict())[0]["available"])
        stale = {**snapshot["environment"], "runtime_build": "new build"}
        self.assertFalse(recommendations([snapshot], self.config.to_dict(),
                                         current_environment=stale)[0]["available"])
        short = {**measured, "runs": [{"index": 1, "tokens": 30,
                                        "generation_seconds": 1.2,
                                        "tokens_per_second": 25.0}], "requested_runs": 1,
                 "warnings": ["short output"]}
        with patch("model_studio.benchmarks.research._gpu_signature", return_value=("GPU-A:1000MiB", "D1")):
            self.assertFalse(prepare_result(self.store, self.config, short)["comparison_eligible"])

    def test_informational_memory_warning_keeps_comparability_and_gpu_sources(self):
        measured = {"status": "completed", "server_version": "llama-build-1",
                    "model": "tag", "models_before": [{"name": "tag"}],
                    "placement_after_warmup": {"context_length": 32768,
                                               "memory": {"vram_bytes": 1200}},
                    "model_info": {"offload": "100%", "memory_source": "startup log",
                                   "context_limit": 32768},
                    "gpu_summary": [{"name": "GPU-A", "peak_vram_bytes": 1700}],
                    "measured_gpu_summary": [{"name": "GPU-A", "peak_vram_bytes": 1600}],
                    "gpu_summary_scope": "warmup + measured requests",
                    "requested_runs": 1, "requested_tokens_per_run": 512,
                    "runs": [{"index": 1, "tokens": 512, "generation_seconds": 8,
                              "tokens_per_second": 64.0}],
                    "summary": {"median_tokens_per_second": 64.0},
                    "warnings": ["Память/offload из лога: проверьте, что он относится к текущему запуску сервера."]}
        with patch("model_studio.benchmarks.research.environment_snapshot", return_value={
                "backend": "ollama", "runtime_build": "build", "hardware": "GPU-A",
                "driver": "D1", "verified": True}):
            good = prepare_result(self.store, self.config, measured)
            contaminated = prepare_result(self.store, self.config, {
                **measured, "models_before": [{"name": "tag"}, {"name": "other"}]})
            short = prepare_result(self.store, self.config, {
                **measured, "runs": [{"index": 1, "tokens": 100,
                                      "generation_seconds": 8, "tokens_per_second": 12.5}]})
        self.assertTrue(good["comparison_eligible"])
        self.assertEqual(good["warnings"], measured["warnings"])
        self.assertEqual(good["gpu_peak_bytes"], 1600)
        self.assertEqual(good["memory"]["vram_bytes"], 1200)
        self.assertEqual(good["model_info"]["offload"], "100%")
        self.assertEqual(good["model_info"]["memory_source"], "startup log")
        self.assertFalse(contaminated["comparison_eligible"])
        self.assertIn("Перед замером в памяти были другие модели", contaminated["blocking_reasons"])
        self.assertFalse(short["comparison_eligible"])
        self.assertIn("Один из прогонов завершился до запрошенной длины ответа", short["blocking_reasons"])

    def test_managed_rounded_context_preserves_measured_evidence(self):
        model = self.store.upsert_model({"backend": "gguf", "locator": "selected.gguf",
                                         "digest": "verified-model", "identity_verified": True})
        config = LaunchConfig(model="selected.gguf", executable="llama-server.exe",
                              context=100000, model_id=model["id"])
        measured = {"status": "completed", "requested_runs": 1,
                    "requested_tokens_per_run": 512,
                    "placement_after_warmup": {"context_length": 100096},
                    "model_info": {"context_limit": 100096,
                                   "context_source": "/props.default_generation_settings.n_ctx"},
                    "runs": [{"index": 1, "tokens": 512, "generation_seconds": 10,
                              "tokens_per_second": 51.2}],
                    "summary": {"median_tokens_per_second": 51.2}}
        environment = {"backend": "llama.cpp", "runtime_build": "v1", "hardware": "GPU-A",
                       "driver": "D1", "verified": True}
        with patch("model_studio.benchmarks.research.environment_snapshot", return_value=environment):
            result = prepare_result(self.store, config, measured)
        self.assertTrue(result["comparison_eligible"])
        self.assertTrue(result["effective_config_verified"])
        self.assertEqual(result["effective_config"]["context"], 100096)
        self.assertEqual(result["context_evidence"]["requested"], 100000)
        cards = recommendations([result], config.to_dict(), current_environment=environment)
        self.assertTrue(cards[0]["available"])
        self.assertTrue(cards[0]["exact_current"])
        self.assertEqual(cards[0]["context"], 100096)

    def test_recommendations_require_comparable_proof_and_long_input(self):
        base = {"id": "one", "status": "completed", "model_id": self.config.model_id,
                "artifact": {"identity_verified": True, "digest": "abc"},
                "environment": {"verified": True, "runtime_build": "v1",
                                "hardware": "GPU-A", "driver": "D1"},
                "effective_config_verified": True,
                "comparison_eligible": True,
                "effective_config": {**self.config.to_dict(), "context": 4096},
                "workload": {"method": "studio-short-v1", "signature": "same"},
                "summary": {"median_tokens_per_second": 25.0},
                "memory": {"vram_bytes": 1000},
                "long_context": {"validated": True, "accepted_tokens": 3900}}
        incomparable = {**base, "id": "other", "summary": {"median_tokens_per_second": 999},
                        "workload": {"method": "other", "signature": "other"}}
        cards = recommendations([base, incomparable], self.config.to_dict(), target_context=4096,
                                current_environment=base["environment"])
        self.assertEqual(cards[0]["result_id"], "one")
        self.assertTrue(cards[1]["available"])
        self.assertTrue(cards[2]["available"])
        self.assertFalse(cards[0]["exact_current"])
        unproved = {**base, "artifact": {"identity_verified": False, "digest": None}}
        self.assertFalse(recommendations([unproved])[0]["available"])
        no_long = {**base, "long_context": {"validated": False}}
        self.assertFalse(recommendations([no_long])[1]["available"])

    def test_report_and_exports_omit_full_paths(self):
        result = {"id": "r1", "status": "completed",
                  "config": {"model": r"C:\private\weights\model.gguf",
                             "executable": r"C:\tools\llama-server.exe"},
                  "workload": {"method": "test"}, "runs": [{"tokens_per_second": 10}],
                  "summary": {"median_tokens_per_second": 10},
                  "warnings": [r"File C:\private\weights\model.gguf changed"]}
        text = report_text(result)
        self.assertIn("Конфигурация", text)
        self.assertNotIn("C:\\private", text)
        output = export_result(result, self.root / "reports", "json")
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"], 1)
        self.assertNotIn("C:\\private", output.read_text(encoding="utf-8"))
