"""One formatter for clipboard/text export and privacy-conscious JSON export."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
from uuid import uuid4


def _private(value, key=""):
    if isinstance(value, dict):
        return {k: _private(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_private(v, key) for v in value]
    if isinstance(value, str) and (key in {"path", "locator", "model", "executable", "model_path", "log_path"}
                                   or key.endswith("_path")):
        return value.replace("\\", "/").split("/")[-1] if "/" in value or "\\" in value else value
    if isinstance(value, str):
        # Diagnostics may embed a path inside prose rather than a path field.
        return re.sub(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\n;,\"')]+", "[локальный путь]", value)
    return value


def report_text(result: dict) -> str:
    row = _private(result)
    lines = ["Модельная студия — результат измерения",
             f"ID: {row.get('id', '—')}", f"Дата: {row.get('created_at', '—')}",
             f"Статус: {row.get('status', '—')}"]
    sections = (("Модель и артефакт", "artifact"), ("Конфигурация", "config"),
                ("Применённая конфигурация", "effective_config"),
                ("Среда", "environment"), ("Методика", "workload"),
                ("Длинный контекст", "long_context"), ("Память", "memory"),
                ("Метаданные модели и источник памяти", "model_info"),
                ("Пик GPU во время замеров", "measured_gpu_summary"),
                ("GPU включая прогрев", "gpu_summary"),
                ("Сводка", "summary"), ("Прогоны", "runs"), ("Ограничения", "warnings"))
    for title, key in sections:
        if key in row and row[key] is not None:
            lines += ["", title + ":", json.dumps(row[key], ensure_ascii=False, indent=2, default=str)]
    for key in ("error", "comparison_limit"):
        if row.get(key):
            lines += ["", f"{key}: {row[key]}"]
    if row.get("blocking_reasons"):
        lines += ["", "Причины исключения из рекомендаций:",
                  json.dumps(row["blocking_reasons"], ensure_ascii=False, indent=2)]
    return "\n".join(lines) + "\n"


def export_result(result: dict, reports_dir: str | Path, format: str = "json") -> Path:
    if format not in ("json", "txt"):
        raise ValueError("format must be json or txt")
    directory = Path(reports_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stem = "result-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    destination = directory / f"{stem}.{format}"
    temporary = directory / f".{stem}.tmp"
    try:
        if format == "json":
            content = json.dumps({"schema_version": 1, "result": _private(result)},
                                 ensure_ascii=False, indent=2, default=str) + "\n"
        else:
            content = report_text(result)
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
