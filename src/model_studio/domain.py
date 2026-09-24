"""Small core types shared by session and presentation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal["stopped", "starting", "ready", "stopping", "failed", "disconnected"]
Busy = Literal["idle", "chat", "benchmark", "research"]


@dataclass(frozen=True)
class StreamChunk:
    kind: Literal["text", "reasoning"]
    text: str


class SessionBusy(RuntimeError):
    """A session command cannot overlap another command."""


class SessionUnavailable(RuntimeError):
    """No ready session can serve the requested operation."""
