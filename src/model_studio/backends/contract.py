"""The narrow runtime boundary shared by chat and measurements."""
from __future__ import annotations

from typing import Iterator, Protocol
import threading

from model_studio.domain import StreamChunk


class Backend(Protocol):
    backend: str
    host: str
    context: int
    model_info: dict

    def prepare(self, model: str) -> dict: ...
    def cancel(self) -> None: ...
    def chat(self, model: str, messages: list[dict], max_tokens: int,
             temperature: float, stop: threading.Event) -> Iterator[StreamChunk | dict]: ...
    def generate(self, model: str, prompt: str, tokens: int, stop: threading.Event) -> dict: ...
