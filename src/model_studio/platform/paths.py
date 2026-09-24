"""User data paths, independent of the source checkout and working directory."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("MODEL_STUDIO_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return (Path(base) / "ModelStudio").resolve()
    # LOCALAPPDATA is absent on some development and test hosts.
    return (Path.home() / ".local" / "share" / "ModelStudio").resolve()


def paths(root: str | Path | None = None) -> dict[str, Path]:
    base = Path(root).expanduser().resolve() if root is not None else data_dir()
    return {name: base / child for name, child in {
        "database": "studio.db", "reports": "reports", "logs": "logs",
        "cache": "cache", "backups": "backups",
    }.items()}


def ensure_data_dirs(root: str | Path | None = None) -> dict[str, Path]:
    result = paths(root)
    result["database"].parent.mkdir(parents=True, exist_ok=True)
    for name in ("reports", "logs", "cache", "backups"):
        result[name].mkdir(parents=True, exist_ok=True)
    return result
