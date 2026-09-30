"""Fixed transcript replay: six prefill/decode cycles, no executed tools or grading.

Canonical assistant continuations keep subsequent inputs identical even when KV
quantization changes generated text. The shared prefix remains cacheable; only
the preceding short decode is replaced at a turn boundary.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
import json
from math import isfinite
import statistics
import time

from engine import Cancelled, run_benchmark

METHOD = "studio-agent-v1"
TURNS = 6
TOKENS_PER_TURN = 128
OUTPUT_TOKENS = TURNS * TOKENS_PER_TURN
TARGET_TOKENS = 24576
MIN_CONTEXT = TARGET_TOKENS + TOKENS_PER_TURN + 32
OPTIONS = {"temperature": 0, "seed": 42, "n_predict": TOKENS_PER_TURN,
           "ignore_eos": True, "stop": [], "cache_prompt": "cold-first/shared-prefix",
           "samplers": ["temperature"]}
INTRO = ("System: You are a coding assistant inspecting a local project. Read the supplied "
         "files and tool results, then explain the next implementation step. Tools are simulated.\n"
         "User: Investigate request routing, caching and retries. Describe a safe implementation.\n")
SUFFIX = "\nAssistant: The next implementation step is"


def tool_boundary(turn):
    return ("\nAssistant: Inspect the next files and validate cache invalidation.\n"
            f"Tool result {turn}: fixed repository snapshot\n")


def fixture(turn):
    """Versioned, varied repository/log material; no filesystem or random input."""
    blocks = []
    for index in range(160):
        key = f"module_{turn}_{index:03d}"
        blocks.append(f"File: src/{key}.py\n"
            f"def route_{index}(request, cache):\n"
            f"    key = ('{key}', request.method, request.path)\n"
            "    cached = cache.get(key)\n"
            "    if cached is not None:\n        return cached\n"
            f"    response = dispatch(request, retries={index % 4}, timeout={10 + index % 17})\n"
            "    if response.status == 200:\n        cache[key] = response\n"
            "    return response\n"
            f"Test: test_{key}_cache passed; requests={index + 1}; misses={index % 7}\n")
    return "\n".join(blocks)


@lru_cache(maxsize=1)
def prompt_digest():
    return hashlib.sha256((INTRO + SUFFIX + str(TARGET_TOKENS) +
                           "".join(tool_boundary(i) + fixture(i) for i in range(TURNS))).encode()).hexdigest()


def workload(runs):
    material = {"method": METHOD, "prompt_digest": prompt_digest(), "runs": runs,
                "tokens": OUTPUT_TOKENS, "options": OPTIONS}
    return {"method": METHOD, "signature": hashlib.sha256(json.dumps(material,
            sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "prompt_digest": material["prompt_digest"], "runs_requested": runs,
            "tokens_per_run": OUTPUT_TOKENS, "options": dict(OPTIONS),
            "turns": TURNS, "target_prompt_tokens": TARGET_TOKENS,
            "history_policy": "canonical-replay"}


def scenario_seconds(row):
    value = (row.get("summary") or {}).get("median_scenario_seconds")
    return value if type(value) in (int, float) and isfinite(value) and value > 0 else None


class AgentScenario:
    def prepare(self, client, stop):
        if client.backend != "llama.cpp":
            raise ValueError("Агентский тест требует llama.cpp с /tokenize и /completion; "
                             "Ollama пока не поддерживает эту методику.")
        if client.context < MIN_CONTEXT:
            raise ValueError(f"Для фиксированного агентского сценария нужен контекст ≥{MIN_CONTEXT} "
                             "(выберите 32K или больше). Сценарий не сокращается.")
        def tokenize(text, special=False):
            if stop.is_set():
                raise Cancelled()
            ids = client.request("/tokenize", {"content": text, "add_special": special,
                                  "parse_special": False, "with_pieces": False}, timeout=30).get("tokens")
            if not isinstance(ids, list) or not ids or any(type(t) is not int for t in ids):
                raise ValueError("Runtime не вернул токены сценария")
            return ids
        history = tokenize(INTRO, True)
        suffix = tokenize(SUFFIX)
        self.prompts = []
        for turn in range(TURNS):
            if turn:
                history += tokenize(tool_boundary(turn))
            data = tokenize(fixture(turn))
            target = (turn + 1) * TARGET_TOKENS // TURNS
            needed = target - len(history) - len(suffix)
            if not 0 < needed <= len(data):
                raise ValueError("Токенизатор не позволяет собрать фиксированный сценарий")
            history += data[:needed] + suffix
            self.prompts.append(list(history))
        self.token_digest = hashlib.sha256(json.dumps(self.prompts).encode()).hexdigest()

    def metadata(self, runs):
        return {"method": METHOD, "workload": workload(runs), "options": dict(OPTIONS),
                "prompt": INTRO, "scenario_token_digest": getattr(self, "token_digest", None),
                "prompt_cache_policy": "cold first turn of every repeat; shared prefix thereafter"}

    def run(self, client, model, emit, stop):
        turns = []
        started = time.perf_counter()
        for index, prompt in enumerate(self.prompts):
            if stop.is_set():
                raise Cancelled()
            emit("status", f"Агентский сценарий · ход {index + 1}/{TURNS} · история {len(prompt)} токенов")
            payload = {**OPTIONS, "model": model, "prompt": prompt, "stream": True,
                       "cache_prompt": index > 0, "id_slot": 0}
            stream = client.stream("/completion", payload, stop, sse=True)
            final, first, elapsed = None, None, 0
            try:
                for part, elapsed in stream:
                    if part.get("error"):
                        raise RuntimeError(str(part["error"]))
                    if first is None and (part.get("content") or part.get("tokens")):
                        first = elapsed
                    if part.get("stop") is True:
                        final = part
                        break
            finally:
                stream.close()
            if stop.is_set():
                raise Cancelled()
            if final is None:
                raise RuntimeError("Поток агентского теста оборвался до итоговых счётчиков")
            timing = final.get("timings") or {}
            output = timing.get("predicted_n")
            processed, cached = timing.get("prompt_n"), timing.get("cache_n")
            accepted = final.get("tokens_evaluated")
            if (final.get("truncated") or accepted != len(prompt) or output != TOKENS_PER_TURN
                    or type(processed) is not int or processed <= 0
                    or (index == 0 and processed != len(prompt))):
                raise RuntimeError("Runtime не подтвердил полный вход, холодный старт или фиксированную длину хода")
            decode, prefill = timing.get("predicted_ms"), timing.get("prompt_ms")
            if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in (decode, prefill)):
                raise RuntimeError("Runtime не вернул корректное время prefill/decode")
            turns.append({"index": index + 1, "tokens": output, "prompt_tokens": accepted,
                          "prompt_processed_tokens": processed, "prompt_cached_tokens": cached,
                          "generation_seconds": decode / 1000, "prompt_seconds": prefill / 1000,
                          "wall_seconds": elapsed, "ttft_seconds": first,
                          "draft_n": timing.get("draft_n"), "draft_n_accepted": timing.get("draft_n_accepted")})
        wall = time.perf_counter() - started
        total = {k: sum(t[k] for t in turns) for k in
                 ("tokens", "generation_seconds", "prompt_seconds", "prompt_processed_tokens")}
        for key in ("draft_n", "draft_n_accepted", "prompt_cached_tokens"):
            total[key] = sum(t[key] for t in turns) if all(type(t[key]) is int for t in turns) else None
        return {**total, "output_tokens": total["tokens"], "turns": turns,
                "wall_seconds": wall, "scenario_seconds": wall,
                "prompt_tokens": TARGET_TOKENS, "tokens_per_second": total["tokens"] / total["generation_seconds"],
                "ttft_seconds": turns[0]["ttft_seconds"]}

    def summary(self, runs):
        return {"median_scenario_seconds": statistics.median(r["scenario_seconds"] for r in runs),
                "total_scenario_seconds": sum(r["scenario_seconds"] for r in runs),
                "total_prefill_seconds": sum(r["prompt_seconds"] for r in runs),
                "total_decode_seconds": sum(r["generation_seconds"] for r in runs)}


def run_agent_benchmark(client, model, emit, stop, output_dir=None, runs=1):
    return run_benchmark(client, model, emit, stop, output_dir, runs=runs,
                         tokens=OUTPUT_TOKENS, workload=AgentScenario())
