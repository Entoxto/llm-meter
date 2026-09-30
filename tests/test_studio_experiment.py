from contextlib import contextmanager
from agent_fixtures import agent_measurement
from model_studio.benchmarks.agent import METHOD, OUTPUT_TOKENS
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_studio.benchmarks import (compose_evidence, plan_candidates, preview_experiment,
                                     recommendations, run_research, resume_research)
from model_studio.configuration import LaunchConfig
from model_studio.storage import Store


ENV = {"backend": "llama.cpp", "runtime_build": "build-1", "hardware": "GPU-A",
       "driver": "D1", "verified": True, "projector_verified": True}
PROOF = {"validated": True, "requested_context": 32768, "observed_context": 32768,
         "tokenized_input": 32000, "reported_prompt_tokens": 32000,
         "accepted_tokens": 32000, "output_tokens": 16}


class Client:
    backend = "llama.cpp"
    host = "http://127.0.0.1:8081"
    def __init__(self):
        self.context = 0
    def resident(self, model):
        return {"context_length": self.context, "memory": {"vram_bytes": 1000}}


class Session:
    def __init__(self):
        self.client = Client()
        self.model_id = "runtime"
        self.snapshot = {"status": "stopped", "config": None}
        self.stop = threading.Event()
        self.started = []
        self.benchmarks = 0
        self.on_benchmark = None
        self.unloads = 0
    @property
    def cancel_event(self):
        return self.stop
    @contextmanager
    def research_operation(self):
        yield self
    def start(self, config):
        if self.stop.is_set():
            return {"status": "stopped"}
        self.started.append(config)
        self.client.context = config.context
        self.snapshot = {"status": "ready", "config": config.to_dict()}
        return self.snapshot
    def benchmark(self, runs, tokens=OUTPUT_TOKENS):
        self.benchmarks += 1
        result = {"status": "completed", "session_id": "session", "runtime_model_id": self.model_id,
                  "requested_runs": runs, "requested_tokens_per_run": tokens,
                  "runs": [{"index": i, "tokens": tokens, "generation_seconds": 10,
                            "tokens_per_second": 51.2,
                            "draft_n": self.snapshot["config"]["draft"] if self.snapshot["config"]["mtp"] else None}
                           for i in range(1, runs + 1)],
                  "summary": {"median_tokens_per_second": 51.2}}
        result.update(agent_measurement(runs, draft=self.snapshot["config"]["draft"] if self.snapshot["config"]["mtp"] else None))
        if self.on_benchmark:
            self.on_benchmark()
        return result
    def cancel(self):
        self.stop.set()
    def begin_restoration(self):
        self.stop = threading.Event()
    def unload(self):
        self.unloads += 1
        self.snapshot = {"status": "stopped", "config": None}


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "studio.db")
        model = self.store.upsert_model({"backend": "gguf", "locator": "test.gguf", "name": "test",
                                         "digest": "digest", "identity_verified": True})
        self.config = LaunchConfig(model="test.gguf", model_id=model["id"], executable="llama-server.exe",
                                   capabilities=("mtp", "kv-cache"), context=32768)
        self.plan = {"scope": "experiment", "contexts": [32768, 65536], "kv_types": ["f16", "q8_0"],
                     "mtp_variants": [0, 2], "checks": {"speed": True, "long_context": True},
                     "runs": 2, "acknowledged_external": True, "environment": ENV}
        self.env_patch = patch("model_studio.benchmarks.research.environment_snapshot", return_value=ENV)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_cartesian_48_and_arbitrary_mtp_subsets(self):
        plan = {**self.plan, "contexts": [32768, 65536, 98304, 131072],
                "kv_types": ["f16", "q8_0", "q4_0"], "mtp_variants": [0, 1, 2, 4]}
        rows = plan_candidates(self.config, plan)
        self.assertEqual(len(rows), 48)
        self.assertEqual(len({r["key"] for r in rows}), 48)
        preview = preview_experiment(self.store, self.config, plan)
        self.assertEqual((preview["total"], preview["speed_checks"], preview["long_checks"],
                          preview["checks_total"]), (48, 48, 48, 96))
        self.assertEqual(len(plan_candidates(self.config, {**plan, "mtp_variants": [4, 0]})), 24)
        self.assertEqual([r["config"].draft for r in plan_candidates(
            self.config, {**plan, "mtp_variants": [3, 0]}) if r["config"].mtp][:1], [3])
        with self.assertRaisesRegex(ValueError, "256 конфигураций"):
            plan_candidates(self.config, {**plan, "contexts": list(range(32768, 32768 + 257))})

    def test_old_speed_results_are_not_reused_and_old_jobs_cannot_resume(self):
        plan = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0],
                "checks": {"speed": True, "long_context": False}}
        session = Session()
        job = run_research(session, self.store, self.config, plan)
        source = self.store.results(self.config.model_id)[0]
        legacy = {**source, "workload": {"method": "legacy-short-v2", "signature": "old"}}
        with patch.object(self.store, "results", return_value=[legacy]):
            preview = preview_experiment(self.store, self.config, plan)
        self.assertEqual(preview["rows"][0]["speed_status"], "measure")
        self.assertEqual(self.store.results(self.config.model_id)[0], source)
        old_plan = {**job["plan"], "benchmark_method": "legacy-short-v2"}
        self.store.update_research(job["id"], {"status": "interrupted", "plan": old_plan})
        with self.assertRaisesRegex(ValueError, "Методика исследования изменилась"):
            resume_research(Session(), self.store, job["id"])

    def test_small_context_rejected_before_start_but_long_only_still_allowed(self):
        with self.assertRaisesRegex(ValueError, "историю 24K"):
            preview_experiment(self.store, self.config, {**self.plan, "contexts": [4096]})
        preview = preview_experiment(self.store, self.config, {**self.plan, "contexts": [4096],
                                      "checks": {"speed": False, "long_context": True}})
        self.assertEqual(preview["speed_checks"], 0)

    def test_partial_reuse_and_long_only(self):
        single = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0]}
        proof = PROOF
        with patch("model_studio.benchmarks.research._long_context_probe", return_value=proof) as probe:
            speed = run_research(Session(), self.store, self.config,
                                 {**single, "checks": {"speed": True, "long_context": False}})
            preview = preview_experiment(self.store, self.config, single)
            self.assertEqual((preview["rows"][0]["speed_status"], preview["rows"][0]["long_status"]),
                             ("history", "measure"))
            session = Session()
            job = run_research(session, self.store, self.config,
                               {**single, "preview_signature": preview["signature"]})
            self.assertEqual(session.benchmarks, 0)
            self.assertEqual(probe.call_count, 1)
            step = job["completed_steps"][0]
            self.assertEqual(step["speed_result_id"], speed["completed_steps"][0]["speed_result_id"])
            self.assertNotEqual(step["long_result_id"], step["speed_result_id"])
            preview2 = preview_experiment(self.store, self.config, single)
            self.assertEqual((preview2["rows"][0]["speed_status"], preview2["rows"][0]["long_status"]),
                             ("history", "history"))
            self.assertEqual(len(self.store.results(self.config.model_id)), 2)

    def test_environment_mismatch_isolation_and_stale_preview(self):
        single = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0],
                  "checks": {"speed": True, "long_context": False}}
        old = preview_experiment(self.store, self.config, single)
        run_research(Session(), self.store, self.config, single)
        with self.assertRaisesRegex(ValueError, "обновите предварительный просмотр"):
            run_research(Session(), self.store, self.config, {**single, "preview_signature": old["signature"]})
        with patch("model_studio.benchmarks.research.environment_snapshot",
                   return_value={**ENV, "driver": "D2"}):
            changed = {k: v for k, v in single.items() if k != "environment"}
            self.assertEqual(preview_experiment(self.store, self.config, changed)["rows"][0]["speed_status"],
                             "measure")

    def test_long_only_does_not_benchmark_and_unverified_proof_is_not_reused(self):
        plan = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0],
                "checks": {"speed": False, "long_context": True}}
        with patch("model_studio.benchmarks.research._long_context_probe",
                   return_value={"validated": False, "reason": "counters unavailable"}):
            first_session = Session()
            first = run_research(first_session, self.store, self.config, plan)
        self.assertEqual(first_session.benchmarks, 0)
        self.assertIsNone(first["completed_steps"][0]["speed_result_id"])
        self.assertEqual(preview_experiment(self.store, self.config, plan)["rows"][0]["long_status"], "measure")
        with patch("model_studio.benchmarks.research._long_context_probe",
                   return_value=PROOF):
            second_session = Session()
            second = run_research(second_session, self.store, self.config, plan)
        self.assertEqual(second_session.benchmarks, 0)
        self.assertNotEqual(first["completed_steps"][0]["long_result_id"],
                            second["completed_steps"][0]["long_result_id"])
        self.assertEqual(preview_experiment(self.store, self.config, plan)["rows"][0]["long_status"], "history")

    def test_long_history_speed_missing_runs_speed_without_long_probe(self):
        single = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0]}
        long_only = {**single, "checks": {"speed": False, "long_context": True}}
        with patch("model_studio.benchmarks.research._long_context_probe",
                   return_value=PROOF) as probe:
            old = run_research(Session(), self.store, self.config, long_only)
            preview = preview_experiment(self.store, self.config, single)
            self.assertEqual((preview["rows"][0]["speed_status"], preview["rows"][0]["long_status"]),
                             ("measure", "history"))
            session = Session()
            job = run_research(session, self.store, self.config, single)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(session.benchmarks, 1)
        self.assertEqual(job["completed_steps"][0]["long_result_id"],
                         old["completed_steps"][0]["long_result_id"])
        self.assertNotEqual(job["completed_steps"][0]["speed_result_id"],
                            job["completed_steps"][0]["long_result_id"])

    def test_proof_isolated_by_kv_and_mtp_and_mtp_requires_counters(self):
        plan = {**self.plan, "contexts": [32768], "mtp_variants": [0, 2],
                "checks": {"speed": False, "long_context": True}}
        with patch("model_studio.benchmarks.research._long_context_probe",
                   return_value=PROOF):
            run_research(Session(), self.store, self.config, plan)
        preview = preview_experiment(self.store, self.config, plan)
        statuses = {(r["kv_type"], r["draft"]): r["long_status"] for r in preview["rows"]}
        self.assertEqual(statuses[("f16", 0)], "history")
        self.assertEqual(statuses[("q8_0", 0)], "history")
        self.assertEqual(statuses[("f16", 2)], "measure")
        self.assertEqual(statuses[("q8_0", 2)], "measure")
        with patch("model_studio.benchmarks.research._long_context_probe",
                   return_value={**PROOF, "draft_n": 2}):
            run_research(Session(), self.store, self.config, plan)
        self.assertTrue(all(r["long_status"] == "history" for r in
                            preview_experiment(self.store, self.config, plan)["rows"]))

    def test_all_history_preserves_existing_session_and_composes_recommendation(self):
        single = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0]}
        with patch("model_studio.benchmarks.research._long_context_probe", return_value=PROOF):
            run_research(Session(), self.store, self.config, single)
        initial = {"status": "ready", "config": self.config.to_dict()}
        session = Session()
        session.snapshot = initial
        job = run_research(session, self.store, self.config, single)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(session.started, [])
        self.assertEqual(session.unloads, 0)
        rows = self.store.results(self.config.model_id)
        source = next(r for r in rows if (r.get("workload") or {}).get("method") == METHOD)
        projected = next(r for r in compose_evidence(rows) if r["id"] == source["id"])
        self.assertTrue(projected["long_context"]["validated"])
        self.assertEqual(projected["created_at"], source["created_at"])
        self.assertNotIn("long_result_id", source)
        cards = recommendations(rows, self.config.to_dict(), target_context=32768,
                                current_environment=ENV)
        self.assertTrue(cards[1]["available"])

    def test_cancel_and_resume(self):
        plan = {**self.plan, "checks": {"speed": True, "long_context": False}}
        session = Session()
        session.on_benchmark = session.cancel
        first = run_research(session, self.store, self.config, plan)
        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(len(first["completed_steps"]), 1)
        session.on_benchmark = None
        resumed = resume_research(session, self.store, first["id"])
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(len(resumed["completed_steps"]), 8)
        self.assertEqual(len(self.store.results(self.config.model_id)), 8)

    def test_cancel_after_long_proof_reuses_it_on_resume(self):
        plan = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0]}
        session = Session()
        def probe(client, model, context, stop, **kwargs):
            session.cancel()
            return PROOF
        with patch("model_studio.benchmarks.research._long_context_probe", side_effect=probe):
            first = run_research(session, self.store, self.config, plan)
        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(first["completed_steps"], [])
        self.assertEqual(session.benchmarks, 0)
        self.assertEqual(first["summary"]["pending"], 1)
        proof_id = self.store.results(self.config.model_id)[0]["id"]
        with patch("model_studio.benchmarks.research._long_context_probe") as probe_again:
            resumed = resume_research(session, self.store, first["id"])
        self.assertFalse(probe_again.called)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["completed_steps"][0]["long_result_id"], proof_id)
        self.assertEqual(resumed["summary"]["supplement"], 1)

    def test_resume_without_history_reuses_only_own_partial_checks(self):
        plan = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0],
                "skip_existing": False}
        with patch("model_studio.benchmarks.research._long_context_probe", return_value=PROOF):
            run_research(Session(), self.store, self.config, plan)
        session = Session()
        def probe(*args, **kwargs):
            session.cancel()
            return PROOF
        with patch("model_studio.benchmarks.research._long_context_probe", side_effect=probe) as fresh:
            first = run_research(session, self.store, self.config, plan)
        self.assertEqual(fresh.call_count, 1)  # Foreign history remains disabled.
        self.assertEqual(first["status"], "cancelled")
        own = next(r for r in self.store.results() if r.get("research_id") == first["id"])
        with patch("model_studio.benchmarks.research._long_context_probe") as repeated:
            resumed = resume_research(session, self.store, first["id"])
        repeated.assert_not_called()
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["completed_steps"][0]["long_result_id"], own["id"])
        self.assertEqual(session.benchmarks, 1)
        self.assertEqual(len([r for r in self.store.results() if r.get("research_id") == first["id"]]), 2)

    def test_indexed_composition_preserves_provenance_and_identity_boundaries(self):
        from copy import deepcopy
        plan = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0]}
        with patch("model_studio.benchmarks.research._long_context_probe", return_value=PROOF):
            run_research(Session(), self.store, self.config, plan)
        rows = self.store.results()
        speed = next(r for r in rows if (r.get("workload") or {}).get("method") == METHOD)
        proof = next(r for r in rows if (r.get("workload") or {}).get("method") == "studio-long-v1")
        invalid = []
        for field, key, value in (("environment", "driver", "D2"),
                                  ("artifact", "digest", "different"),
                                  ("config", "kv_type", "q8_0"),
                                  ("effective_config", "context", 65536)):
            row = deepcopy(proof)
            row[field][key] = value
            invalid.append(row)
        first = deepcopy(proof)
        first["id"] = "first-proof"
        first["config"]["draft"] = 7  # Inactive Draft is not a test condition.
        first["effective_config"]["draft"] = 7
        first["config"]["runtime_name"] = "renamed runtime"
        inputs = [*invalid, first, proof, speed]
        original = deepcopy(inputs)
        projected = compose_evidence(inputs)[-1]
        self.assertEqual(projected["long_result_id"], "first-proof")
        self.assertEqual(projected["long_context_provenance"]["source_created_at"], first["created_at"])
        self.assertEqual(inputs, original)
        self.assertIs(compose_evidence([*invalid, speed])[-1], speed)

    def test_composition_does_not_scan_speed_history_for_every_row(self):
        from model_studio.benchmarks import experiment
        plan = {**self.plan, "contexts": [32768], "kv_types": ["f16"], "mtp_variants": [0],
                "checks": {"speed": True, "long_context": False}}
        run_research(Session(), self.store, self.config, plan)
        source = self.store.results()[0]
        rows = [{**source, "id": str(i)} for i in range(400)]
        with patch.object(experiment, "_same_identity", wraps=experiment._same_identity) as comparisons:
            projected = compose_evidence(rows)
        self.assertEqual(comparisons.call_count, 0)
        self.assertTrue(all(a is b for a, b in zip(rows, projected)))

    def test_failed_variant_is_recorded_and_next_variant_runs(self):
        plan = {**self.plan, "contexts": [32768], "mtp_variants": [0],
                "checks": {"speed": True, "long_context": False}}
        class FailingSession(Session):
            def start(self, config):
                if config.kv_type == "q8_0":
                    raise RuntimeError("KV unavailable")
                return super().start(config)
        job = run_research(FailingSession(), self.store, self.config, plan)
        self.assertEqual(job["status"], "completed")
        self.assertEqual([s["status"] for s in job["completed_steps"]], ["completed", "error"])
        self.assertEqual(job["completed_steps"][1]["error"], "KV unavailable")
        self.assertEqual(preview_experiment(self.store, self.config, plan)["rows"][1]["speed_status"], "measure")


if __name__ == "__main__":
    unittest.main()
