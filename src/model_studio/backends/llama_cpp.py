"""llama-server protocol adapter over the existing cancellable HTTP transport."""
from __future__ import annotations

import threading
from typing import Iterator

from llama_cpp import LlamaCppClient
from model_studio.domain import StreamChunk


class LlamaCppBackend:
    backend = "llama.cpp"

    def __init__(self, host: str, context: int, log_path: str = ""):
        self.client = LlamaCppClient(host, log_path=log_path, context=context)

    def __getattr__(self, name):
        return getattr(self.client, name)

    def prepare(self, model: str) -> dict:
        return self.client.prepare(model)

    def cancel(self) -> None:
        self.client.cancel()

    def generate(self, model: str, prompt: str, tokens: int, stop: threading.Event) -> dict:
        return self.client.generate(model, prompt, tokens, stop)

    def chat(self, model: str, messages: list[dict], max_tokens: int,
             temperature: float, stop: threading.Event) -> Iterator[StreamChunk | dict]:
        payload = {"model": model, "messages": messages, "stream": True,
                   "stream_options": {"include_usage": True}, "max_tokens": max_tokens,
                   "temperature": temperature}
        stream = self.client.stream("/v1/chat/completions", payload, stop, sse=True)
        done, usage, timings, finish_reason = False, {}, {}, None
        elapsed = 0.0
        try:
            for part, elapsed in stream:
                if part.get("_stream_done"):
                    done = True
                    break
                for choice in part.get("choices", []):
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield StreamChunk("reasoning", reasoning)
                    if delta.get("content"):
                        yield StreamChunk("text", delta["content"])
                if part.get("usage"):
                    usage = part["usage"]
                if part.get("timings"):
                    timings = part["timings"]
            if not done:
                raise RuntimeError("llama-server chat stream ended before [DONE].")
            count = timings.get("predicted_n") or usage.get("completion_tokens")
            duration_ms = timings.get("predicted_ms")
            duration = duration_ms / 1000 if isinstance(duration_ms, (int, float)) and duration_ms > 0 else None
            yield {"output_tokens": count, "generation_seconds": duration,
                   "tokens_per_second": count / duration if isinstance(count, (int, float))
                   and count > 0 and duration else None,
                   "prompt_tokens": usage.get("prompt_tokens"), "wall_seconds": elapsed,
                   "done_reason": finish_reason,
                   "timings": timings or None, "usage": usage or None}
        finally:
            stream.close()
