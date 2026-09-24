"""Conservative recommendations computed from comparable saved evidence."""

from __future__ import annotations

from collections import defaultdict
from math import isfinite
from pathlib import Path


_TITLES = {"speed": "Максимальная скорость", "context": "Максимальный контекст",
           "balanced": "Сбалансированный"}
_CONFIG_KEYS = ("backend", "model", "context", "host", "executable", "managed",
                "extra_args", "gpu_layers", "kv_type", "reasoning", "reasoning_budget", "mtp", "draft", "mmproj")


def _card(key: str, reason: str, row: dict | None = None, current: dict | None = None) -> dict:
    config = row.get("effective_config") or row.get("config") or {} if row else {}
    requested = row.get("config") or config if row else {}
    actual_context = config.get("context") if isinstance(config, dict) else None
    speed = (row.get("summary") or {}).get("median_tokens_per_second") if row else None
    exact = False
    if current and row and isinstance(requested, dict):
        exact = all((key in requested or (key == "reasoning_budget" and value is None))
                    and requested.get(key) == value for key, value in current.items()
                    if key in _CONFIG_KEYS)
        exact = exact and any(key in current for key in _CONFIG_KEYS)
    return {"key": key, "title": _TITLES[key], "available": row is not None,
            "reason": reason, "context": actual_context, "speed": speed,
            "result_id": row.get("id") if row else None, "exact_current": exact}


def _eligible(row: dict) -> bool:
    speed = (row.get("summary") or {}).get("median_tokens_per_second")
    environment = row.get("environment") or {}
    artifact = row.get("artifact") or {}
    workload = row.get("workload") or {}
    selected = (row.get("config") or {}).get("mmproj")
    projector = artifact.get("projector")
    module_proven = (not selected and not projector) or bool(
        selected and isinstance(projector, dict) and projector.get("identity_verified") is True
        and projector.get("digest") and environment.get("projector_digest") == projector.get("digest"))
    return (row.get("status") in ("completed", "complete")
            and isinstance(speed, (int, float)) and isfinite(speed) and speed > 0
            and row.get("comparison_eligible") is True
            and isinstance((row.get("effective_config") or {}).get("context"), int)
            and artifact.get("identity_verified") is True and bool(artifact.get("digest"))
            and environment.get("verified") is True
            and module_proven
            and all(environment.get(k) for k in ("runtime_build", "hardware", "driver"))
            and row.get("effective_config_verified") is True
            and bool(workload.get("signature")) and bool(workload.get("method")))


def recommendations(results: list[dict], current_config: dict | None = None,
                    target_context: int = 65536,
                    current_environment: dict | None = None) -> list[dict]:
    """Return three evidence-backed cards; unknowns produce unavailable cards."""
    if current_config is not None and (not current_environment or
                                       current_environment.get("verified") is not True):
        reason = "Среда текущего запуска ещё не проверена; сохранённые замеры доступны в истории."
        return [_card(key, reason) for key in _TITLES]
    if current_config and current_config.get("mmproj"):
        try:
            stat = Path(current_config["mmproj"]).resolve(strict=True).stat()
            same = (current_environment.get("projector_verified") is True
                    and bool(current_environment.get("projector_digest"))
                    and current_environment.get("projector_size_bytes") == stat.st_size
                    and current_environment.get("projector_mtime_ns") == str(stat.st_mtime_ns))
        except OSError:
            same = False
        if not same:
            reason = "Vision projector changed or is unavailable; refresh the runtime environment."
            return [_card(key, reason) for key in _TITLES]
    eligible = [row for row in results if _eligible(row)]
    if current_config:
        current_id = current_config.get("model_id")
        if current_id:
            eligible = [row for row in eligible if row.get("model_id") == current_id]
        eligible = [row for row in eligible if all(
            (row.get("environment") or {}).get(key) == current_environment.get(key)
            for key in ("backend", "runtime_build", "hardware", "driver", "projector_digest"))]
    groups = defaultdict(list)
    for row in eligible:
        artifact = row["artifact"]
        env = row["environment"]
        workload = row["workload"]
        projector = artifact.get("projector") or {}
        key = (row.get("model_id"), artifact["digest"], projector.get("digest"),
               env.get("backend"), env["runtime_build"],
               env["hardware"], env["driver"], workload["signature"])
        groups[key].append(row)
    if not groups:
        reason = "Нет сопоставимых проверенных результатов: нужны digest модели, среда, применённые настройки и методика."
        return [_card(key, reason) for key in _TITLES]
    # Prefer the group containing an exact current configuration, then the
    # largest evidence set. Never compare speeds across different groups.
    def group_priority(rows):
        exact = any(_card("speed", "", row, current_config)["exact_current"] for row in rows)
        newest = max(str(row.get("created_at", "")) for row in rows)
        return (exact, len(rows), newest)
    rows = max(groups.values(), key=group_priority)
    fastest = max(rows, key=lambda r: r["summary"]["median_tokens_per_second"])
    cards = [_card("speed", "Лучшая медианная скорость среди сопоставимых проверенных замеров.",
                   fastest, current_config)]
    long_proven = [r for r in rows if (r.get("long_context") or {}).get("validated") is True
                   and isinstance((r.get("long_context") or {}).get("accepted_tokens"), int)]
    if long_proven:
        longest = max(long_proven, key=lambda r: r["effective_config"]["context"])
        cards.append(_card("context", "Контекст подтверждён длинным входом без усечения.",
                           longest, current_config))
    else:
        cards.append(_card("context", "Нет подтверждённой проверки длинного входа."))
    target_rows = [r for r in rows if isinstance((r.get("effective_config") or {}).get("context"), int)
                   and r["effective_config"]["context"] >= target_context
                   and (r.get("long_context") or {}).get("validated") is True]
    speed_floor = fastest["summary"]["median_tokens_per_second"] * .8
    balanced = [r for r in target_rows if r["summary"]["median_tokens_per_second"] >= speed_floor
                and isinstance((r.get("memory") or {}).get("vram_bytes"), (int, float))]
    if balanced:
        choice = min(balanced, key=lambda r: (r["memory"]["vram_bytes"],
                                               -r["summary"]["median_tokens_per_second"]))
        cards.append(_card("balanced", f"Контекст ≥{target_context}, проверенная память и скорость в пределах 20% от лучшей.",
                           choice, current_config))
    else:
        cards.append(_card("balanced", "Нет проверки целевого контекста, памяти и скорости в пределах 20% от лучшей."))
    return cards
