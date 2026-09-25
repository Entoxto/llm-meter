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
    if isinstance(value, str) and (key in {"path", "locator", "model", "mmproj", "executable", "model_path", "log_path"}
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
    if row.get("legacy_source"):
        legacy = {key: row[key] for key in ("model", "backend", "runtime", "context",
                  "requested_context", "requested_runs", "requested_tokens_per_run", "output_limit")
                  if row.get(key) is not None}
        lines += ["", "Параметры импортированного теста:",
                  json.dumps(legacy, ensure_ascii=False, indent=2, default=str)]
    sections = (("Модель и артефакт", "artifact"), ("Конфигурация", "config"),
                ("Применённая конфигурация", "effective_config"),
                ("Подтверждение контекста", "context_evidence"),
                ("Среда", "environment"), ("Методика", "workload"),
                ("Длинный контекст", "long_context"), ("Память", "memory"),
                ("Метаданные модели и источник памяти", "model_info"),
                ("Пик GPU во время замеров", "measured_gpu_summary"),
                ("GPU включая прогрев", "gpu_summary"),
                ("Сводка", "summary"), ("Прогоны", "runs"),
                ("Сценарии импортированного теста", "cases"), ("Ограничения", "warnings"))
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


def reports_text(results: list[dict], title: str, research: dict | None = None) -> str:
    """One pasteable report, with scope and every saved result kept explicit."""
    lines = ["Модельная студия — сводный отчёт", _private(title),
             f"Снимок на: {datetime.now().astimezone().isoformat(timespec='seconds')}",
             f"Сохранённых замеров: {len(results)}",
             "Проанализируй результаты, объясни компромиссы скорости, контекста и памяти. "
             "Учитывай разные условия тестов, ошибки и неподтверждённые показатели; "
             "не считай отсутствующие значения нулевыми."]
    if research is not None:
        job = _private(research)
        metadata = {key: job.get(key) for key in (
            "id", "created_at", "updated_at", "status", "stop_reason", "error", "restore_error")}
        plan = job.get("plan") or {}
        metadata["plan"] = {key: plan[key] for key in (
            "base_config", "contexts", "target_context", "max_configs", "budget_minutes", "runs", "memory_economy")
            if key in plan}
        lines += ["", "Исследование:", json.dumps(metadata, ensure_ascii=False, indent=2)]
        expected = {step.get("result_id") for step in job.get("completed_steps", []) if step.get("result_id")}
        missing = expected - {row.get("id") for row in results}
        if missing:
            lines.append(f"Недоступно сохранённых замеров из задания: {len(missing)}.")
        if job.get("status") == "running":
            lines.append("Исследование ещё идёт; включены только уже сохранённые замеры.")
    if not results:
        lines.append("Сохранённых замеров пока нет; выше приведено состояние задания.")
    else:
        lines += ["", "Краткое сравнение:",
                  "№ | Запрошенный контекст | Фактический контекст | Генерация, ток/с | TTFT, с | Статус"]
        for index, row in enumerate(results, 1):
            config, summary = row.get("config") or {}, row.get("summary") or {}
            def number(value):
                return f"{value:.3f}".rstrip("0").rstrip(".") if isinstance(value, (int, float)) else "—"
            values = [config.get("context", row.get("requested_context", row.get("context"))),
                      (row.get("effective_config") or {}).get("context"),
                      summary.get("median_tokens_per_second"), summary.get("median_ttft_seconds")]
            lines.append(f"{index} | " + " | ".join(number(v) for v in values) + " | " + str(row.get("status") or "исторический"))
        for index, row in enumerate(results, 1):
            lines += ["", f"{'=' * 20} Замер {index} из {len(results)} {'=' * 20}", report_text(row)]
    return "\n".join(lines) + "\n"
