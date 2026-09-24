"""Discover supported server options without loading a model (worker only)."""
from functools import lru_cache
from pathlib import Path
import re
import subprocess


def runtime_capabilities(executable: str) -> list[str]:
    try:
        path = Path(executable).resolve(strict=True)
        stat = path.stat()
        if not path.is_file():
            return []
    except OSError:
        return []
    return list(_probe(str(path), stat.st_size, stat.st_mtime_ns))


@lru_cache(maxsize=32)
def _probe(executable: str, size: int, mtime_ns: int) -> tuple[str, ...]:
    try:
        result = subprocess.run([executable, "--help"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    help_text = result.stdout + "\n" + result.stderr
    # Only complete option names: --reasoning-format does not enable thinking.
    options = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*(?=[\s,=]|$)", help_text))
    features = []
    if "--reasoning" in options:
        features.append("reasoning")
    if {"--cache-type-k", "--cache-type-v"} <= options:
        features.append("kv-cache")
    return tuple(features)
