"""Publish the active runtime connection for the external Codex switcher.

The manifest contains no credentials, prompts, or persistent catalog data.
It describes a running session, never the unlaunched settings draft.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile


def publish_session(logs_dir: str | Path, snapshot: dict) -> Path:
    root = Path(logs_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "codex-session.json"
    payload = {
        "schema_version": 1,
        "owner_pid": os.getpid(),
        "published_at": datetime.now(timezone.utc).isoformat(),
        "status": snapshot.get("status", "stopped"),
        "busy": snapshot.get("busy", "idle"),
        "session_id": snapshot.get("session_id"),
        "backend": snapshot.get("backend"),
        "host": snapshot.get("host"),
        "model": snapshot.get("model_id"),
        "context": snapshot.get("effective_context") or snapshot.get("context"),
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                         prefix=".codex-session-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
