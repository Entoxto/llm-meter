"""Measured scenario fixtures for orchestration tests (no runtime or GPU)."""
from model_studio.benchmarks.agent import METHOD, OUTPUT_TOKENS, TURNS, TARGET_TOKENS, workload


def agent_measurement(runs=1, duration=30, speed=25, draft=None):
    return {"method": METHOD, "workload": workload(runs), "status": "completed",
            "requested_runs": runs, "requested_tokens_per_run": OUTPUT_TOKENS,
            "runs": [{"index": i + 1, "tokens": OUTPUT_TOKENS, "prompt_tokens": TARGET_TOKENS,
                      "generation_seconds": OUTPUT_TOKENS / speed, "tokens_per_second": speed,
                      "scenario_seconds": duration, "draft_n": draft,
                      "turns": [{"index": t + 1, "tokens": OUTPUT_TOKENS // TURNS,
                                 "prompt_tokens": (t + 1) * TARGET_TOKENS // TURNS}
                                for t in range(TURNS)]} for i in range(runs)],
            "summary": {"median_scenario_seconds": duration, "median_tokens_per_second": speed}}
