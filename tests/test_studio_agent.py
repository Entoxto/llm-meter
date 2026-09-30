"""Agent performance contract, using only a deterministic fake runtime."""
from copy import deepcopy
import threading
import unittest
from unittest.mock import patch

from engine import Cancelled
from agent_fixtures import agent_measurement
from model_studio.benchmarks.agent import (AgentScenario, METHOD, MIN_CONTEXT,
    OUTPUT_TOKENS, TARGET_TOKENS, TURNS, run_agent_benchmark, workload)
from model_studio.benchmarks import recommendations


class Runtime:
    backend = "llama.cpp"
    context = 32768
    context_rounding_tolerance = 0
    host = "http://fake"
    local = False
    model_info = {"context_limit": 32768}

    def __init__(self):
        self.requests = []
        self.warmups = 0
        self.mutate = lambda part: None
        self.closed = 0

    def request(self, path, payload=None, timeout=None):
        if path != "/tokenize":
            return {}
        return {"tokens": [ord(c) for c in payload["content"]]}

    def prepare(self, model):
        return {}

    def loaded_models(self):
        return [{"name": "model"}]

    def resident(self, model):
        return {"context_length": self.context}

    def generate(self, model, prompt, tokens, stop):
        self.warmups += 1
        return {"tokens": tokens}

    def stream(self, path, payload, stop, sse=False):
        self.requests.append(deepcopy(payload))
        n = len(payload["prompt"])
        cached = n - 4100 if payload["cache_prompt"] else 0
        final = {"stop": True, "tokens_evaluated": n, "truncated": False,
                 "timings": {"predicted_n": 128, "predicted_ms": 1000,
                             "prompt_n": n - cached, "prompt_ms": 500,
                             "cache_n": cached, "draft_n": 256, "draft_n_accepted": 100}}
        try:
            yield {"content": "arbitrary unchecked output"}, .5
            self.mutate(final)
            yield final, 1.5
        finally:
            self.closed += 1


class AgentTests(unittest.TestCase):
    def test_fixed_growing_history_and_cold_repeats_with_real_runner(self):
        runtime = Runtime()
        with patch("engine.Telemetry.snapshot", return_value={}):
            result = run_agent_benchmark(runtime, "model", lambda *_: None, threading.Event(), runs=2)
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["method"], METHOD)
        self.assertEqual(result["workload"], workload(2))
        self.assertEqual(result["requested_tokens_per_run"], OUTPUT_TOKENS)
        self.assertEqual(runtime.warmups, 1)
        self.assertEqual(runtime.closed, 12)
        self.assertEqual(runtime.requests[:6], runtime.requests[6:])
        self.assertEqual([len(r["prompt"]) for r in runtime.requests[:6]],
                         [4096, 8192, 12288, 16384, 20480, TARGET_TOKENS])
        for index, request in enumerate(runtime.requests):
            self.assertEqual(request["cache_prompt"], bool(index % TURNS))
            self.assertEqual((request["n_predict"], request["temperature"], request["seed"]), (128, 0, 42))
            self.assertTrue(request["ignore_eos"])
            if index % TURNS:
                previous = runtime.requests[index - 1]["prompt"]
                self.assertEqual(request["prompt"][:len(previous)], previous)
        self.assertEqual(result["runs"][0]["draft_n"], 1536)
        self.assertEqual(result["summary"]["total_prefill_seconds"], 6)
        self.assertEqual(result["summary"]["total_decode_seconds"], 12)
        self.assertGreater(result["summary"]["median_scenario_seconds"], 0)

    def test_configuration_context_does_not_resize_scenario(self):
        first, second = Runtime(), Runtime()
        second.context = 131072
        a, b = AgentScenario(), AgentScenario()
        a.prepare(first, threading.Event())
        b.prepare(second, threading.Event())
        self.assertEqual(a.prompts, b.prompts)
        first.context = MIN_CONTEXT - 1
        with self.assertRaisesRegex(ValueError, "Сценарий не сокращается"):
            AgentScenario().prepare(first, threading.Event())

    def test_reject_short_truncated_missing_counters_and_warm_first_turn(self):
        for change in (lambda r: r.update(truncated=True),
                       lambda r: r.update(tokens_evaluated=10),
                       lambda r: r["timings"].update(predicted_n=127),
                       lambda r: r["timings"].update(prompt_n=100),
                       lambda r: r["timings"].update(predicted_ms=float("nan")),
                       lambda r: r.update(stop=False)):
            with self.subTest(change=change):
                runtime = Runtime()
                runtime.mutate = change
                scenario = AgentScenario()
                scenario.prepare(runtime, threading.Event())
                with self.assertRaises(RuntimeError):
                    scenario.run(runtime, "model", lambda *_: None, threading.Event())
                self.assertEqual(runtime.closed, 1)

    def test_cancel_closes_stream_and_stops_before_next_turn(self):
        stop = threading.Event()
        runtime = Runtime()
        runtime.mutate = lambda r: stop.set()
        scenario = AgentScenario()
        scenario.prepare(runtime, stop)
        with self.assertRaises(Cancelled):
            scenario.run(runtime, "model", lambda *_: None, stop)
        self.assertEqual(runtime.closed, 1)
        self.assertEqual(len(runtime.requests), 1)

    def test_recommendations_choose_elapsed_time_and_exclude_legacy(self):
        common = {"status": "completed", "comparison_eligible": True,
                  "effective_config_verified": True, "model_id": "one",
                  "effective_config": {"context": 32768},
                  "artifact": {"identity_verified": True, "digest": "same"},
                  "environment": {"verified": True, "runtime_build": "v1", "hardware": "gpu", "driver": "d1"}}
        quick = {**common, **agent_measurement(duration=10, speed=20), "id": "quick"}
        slow = {**common, **agent_measurement(duration=30, speed=100), "id": "slow"}
        legacy = {**common, "id": "legacy", "workload": {"method": "legacy-short-v2", "signature": "old"},
                  "summary": {"median_tokens_per_second": 10000, "median_scenario_seconds": 1}}
        self.assertEqual(recommendations([slow, legacy, quick])[0]["result_id"], "quick")
        self.assertFalse(recommendations([legacy])[0]["available"])


if __name__ == "__main__":
    unittest.main()
