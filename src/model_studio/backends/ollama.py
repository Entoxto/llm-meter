"""Ollama protocol adapter over the existing cancellable HTTP transport."""
from __future__ import annotations

import threading
from typing import Iterator

from engine import Client
from model_studio.domain import StreamChunk


class OllamaBackend:
    backend = "ollama"

    def __init__(self, host: str, context: int):
        self.client = Client(host, context)
        self.client.no_truncate = True
        self.client.reasoning = "auto"
        self.client.keep_alive = -1

    def __getattr__(self, name):
        return getattr(self.client, name)

    def prepare(self, model: str) -> dict:
        try:
            result = self.client.prepare(model)
        except RuntimeError as exc:
            if "Выберите локальную модель для теста GPU/RAM" not in str(exc):
                raise
            # Cloud-backed Ollama models can serve chat, but local GPU/RAM
            # measurements do not describe their remote execution.
            info = self.client.request("/api/show", {"model": model}, timeout=15)
            tag = next((item for item in self.client.list_models() if item.get("name") == model), {})
            self.client.local = False
            self.client.model_info = {"name": model, "context_limit": None,
                                      "memory_source": "remote model; local hardware unavailable"}
            result = {"server_version": self.client.request("/api/version").get("version"),
                      "digest": tag.get("digest"), "model_details": info.get("details") or {},
                      "model_parameters": info.get("parameters"), "remote": True}
        if self.client.reasoning != "auto":
            info = self.client.request("/api/show", {"model": model}, timeout=15)
            values = (info.get("thinking") or {}).get("values") or []
            requested = self.client.reasoning == "on"
            if not any(type(value) is bool and value is requested for value in values):
                raise ValueError(f"Ollama model does not confirm reasoning={self.client.reasoning} support.")
        return result

    def cancel(self) -> None:
        self.client.cancel()

    def generate(self, model: str, prompt: str, tokens: int, stop: threading.Event) -> dict:
        return self.client.generate(model, prompt, tokens, stop)

    def preload(self, model: str, stop: threading.Event) -> dict:
        """Load selected weights and context; require an actual resident match."""
        payload = {"model": model, "prompt": "", "stream": True,
                   "keep_alive": -1, "options": {"num_ctx": self.client.context, "num_predict": 0}}
        stream = self.client.stream("/api/generate", payload, stop)
        done = False
        try:
            for part, _ in stream:
                if part.get("done"):
                    done = True
                    break
            if not done:
                raise RuntimeError("Ollama preload stream ended before the final event.")
        finally:
            stream.close()
        resident = self.client.resident(model)
        actual = resident.get("context_length")
        if actual != self.client.context:
            raise RuntimeError(f"Ollama loaded context {actual!r}; requested {self.client.context}.")
        return resident

    def unload(self, model: str) -> None:
        self.client.request("/api/generate", {"model": model, "keep_alive": 0, "stream": False}, timeout=30)

    def chat(self, model: str, messages: list[dict], max_tokens: int,
             temperature: float, stop: threading.Event) -> Iterator[StreamChunk | dict]:
        payload = {"model": model, "messages": messages, "stream": True,
                   "keep_alive": self.client.keep_alive,
                   "truncate": False,
                   "options": {"num_ctx": self.client.context, "num_predict": max_tokens,
                               "temperature": temperature}}
        if self.client.reasoning != "auto":
            payload["think"] = self.client.reasoning == "on"
        stream = self.client.stream("/api/chat", payload, stop)
        done = False
        try:
            for part, elapsed in stream:
                message = part.get("message") or {}
                if message.get("thinking"):
                    yield StreamChunk("reasoning", message["thinking"])
                if message.get("content"):
                    yield StreamChunk("text", message["content"])
                if part.get("done"):
                    done = True
                    count, duration = part.get("eval_count"), part.get("eval_duration")
                    yield {"output_tokens": count, "generation_seconds": duration / 1e9
                           if isinstance(duration, (int, float)) and duration > 0 else None,
                           "tokens_per_second": count * 1e9 / duration
                           if isinstance(count, (int, float)) and isinstance(duration, (int, float))
                           and count > 0 and duration > 0 else None,
                           "prompt_tokens": part.get("prompt_eval_count"), "wall_seconds": elapsed,
                           "done_reason": part.get("done_reason")}
                    break
            if not done:
                raise RuntimeError("Ollama chat stream ended before its final event.")
        finally:
            stream.close()
