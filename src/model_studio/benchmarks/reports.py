"""One formatter for clipboard/text export and privacy-conscious JSON export."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
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
            "scope", "skip_existing", "base_config", "contexts", "target_context", "max_configs", "budget_minutes", "runs", "memory_economy")
            if key in plan}
        lines += ["", "Исследование:", json.dumps(metadata, ensure_ascii=False, indent=2)]
        reused = sum(bool(step.get("reused")) for step in job.get("completed_steps", []))
        if reused:
            lines.append(f"Использовано сопоставимых замеров из истории: {reused}.")
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


def _model_group(row: dict, index: int) -> tuple:
    """Only verified, dated provenance permits one result to replace another."""
    config = row.get("config") or {}
    effective = row.get("effective_config") or {}
    artifact = row.get("artifact") or {}
    environment = row.get("environment") or {}
    workload = row.get("workload") or {}
    projector = artifact.get("projector") or {}
    try:
        datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        dated = True
    except (KeyError, TypeError, ValueError, AttributeError):
        dated = False
    complete = (dated and isinstance(config, dict) and isinstance(effective, dict)
                and row.get("effective_config_verified") is True
                and bool(effective) and isinstance(artifact, dict)
                and isinstance(environment, dict) and isinstance(workload, dict)
                and artifact.get("identity_verified") is True and bool(artifact.get("digest"))
                and environment.get("verified") is True
                and all(environment.get(k) for k in ("runtime_build", "hardware", "driver"))
                and all(workload.get(k) for k in ("method", "signature"))
                and (not config.get("mmproj") or
                     (projector.get("identity_verified") is True and projector.get("digest"))))
    if not complete:
        return ("unverified", index)
    # Labels, paths, available controls and inactive draft length are not test conditions.
    selected = {key: value for key, value in config.items() if key not in {
        "model", "model_id", "host", "executable", "runtime_name", "capabilities",
        "mmproj", "draft"}}
    if config.get("mtp"):
        selected["draft"] = config.get("draft")
    applied = {key: value for key, value in effective.items() if key not in {
        "model", "model_id", "host", "executable", "runtime_name", "capabilities",
        "mmproj", "draft"}}
    if effective.get("mtp"):
        applied["draft"] = effective.get("draft")
    environment_key = {key: value for key, value in environment.items() if key not in {
        "host", "runtime_name", "runtime_version", "verified"}}
    identity = {"digest": artifact["digest"], "backend": artifact.get("backend"),
                "projector_digest": projector.get("digest") if projector else None}
    return ("verified", json.dumps([selected, applied, identity, environment_key, workload],
                                    sort_keys=True, ensure_ascii=False, default=str))


def _model_row_date(row: dict, index: int) -> tuple:
    try:
        value = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc), index
    except (KeyError, TypeError, ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc), index


def _model_cell(value) -> str:
    if value is None or value == "":
        return "неизвестно"
    if isinstance(value, float):
        value = f"{value:.3f}".rstrip("0").rstrip(".")
    return str(_private(str(value))).replace("|", "/").replace("\n", " ")


def _model_overview_line(row: dict) -> str:
    config = row.get("config") or {}
    effective = row.get("effective_config") or {}
    artifact = row.get("artifact") or {}
    environment = row.get("environment") or {}
    workload = row.get("workload") or {}
    summary = row.get("summary") or {}
    mtp = (f"да/{config.get('draft', 'неизвестно')}" if config.get("mtp") is True else
           "нет" if config.get("mtp") is False else "неизвестно")
    projector = (artifact.get("projector") or {}).get("digest")
    projector_label = str(projector)[:12] if projector else ("нет" if not config.get("mmproj") else "неизвестно")
    env_label = "/".join(_model_cell(environment.get(key)) for key in
                         ("runtime_build", "hardware", "driver"))
    workload_label = "/".join((_model_cell(workload.get("method")),
                                _model_cell(str(workload.get("signature"))[:12] if workload.get("signature") else None)))
    args = config.get("extra_args") or []
    args_label = ("нет" if not args else
                  "sha256:" + hashlib.sha256(json.dumps(args, ensure_ascii=False,
                      default=str).encode()).hexdigest()[:8])
    comparable = ("да" if row.get("comparison_eligible") is True and
                  row.get("effective_config_verified") is True else
                  "нет" if row.get("comparison_eligible") is False or
                  row.get("effective_config_verified") is False else "неподтверждено")
    long_valid = (row.get("long_context") or {}).get("validated")
    long_label = "да" if long_valid is True else "нет" if long_valid is False else "неизвестно"
    values = [row.get("id"), row.get("created_at"), config.get("context", row.get("context")),
              effective.get("context"),
              config.get("kv_type"), mtp, config.get("reasoning"), config.get("reasoning_budget"),
              config.get("gpu_layers"), projector_label, args_label,
              str(artifact.get("digest"))[:12] if artifact.get("digest") else None,
              env_label, workload_label, summary.get("median_tokens_per_second"),
              summary.get("median_ttft_seconds"), comparable, long_label]
    return " | ".join(_model_cell(value) for value in values)


def model_report_text(results: list[dict], title: str) -> str:
    """Current result per proven test condition, followed by all saved history."""
    groups: dict[tuple, dict[str, tuple[dict, int]]] = {}
    for index, row in enumerate(results):
        group = groups.setdefault(_model_group(row, index), {})
        bucket = "completed" if row.get("status") == "completed" else "failed"
        previous = group.get(bucket)
        if previous is None or _model_row_date(row, index) > _model_row_date(*previous):
            group[bucket] = (row, index)
    successful = [group["completed"][0] for group in groups.values() if "completed" in group]
    failed = [group["failed"][0] for group in groups.values()
              if "failed" in group and ("completed" not in group or
                  _model_row_date(*group["failed"]) > _model_row_date(*group["completed"]))]
    successful.sort(key=lambda row: _model_row_date(row, 0), reverse=True)
    failed.sort(key=lambda row: _model_row_date(row, 0), reverse=True)
    header = ("ID | Дата | Запрошенный контекст | Фактический контекст | KV | MTP/Draft | Reasoning | Бюджет | GPU слои | "
              "Проектор | Доп. аргументы | Артефакт | Runtime/оборудование/драйвер | "
              "Методика/подпись | Генерация, ток/с | TTFT, с | Для сравнения | Длинный вход подтверждён")
    research_ids = {row.get("research_id") for row in results if row.get("research_id")}
    without_research = sum(not row.get("research_id") for row in results)
    lines = ["Модельная студия — актуальный срез модели", _model_cell(title),
             f"Снимок на: {datetime.now().astimezone().isoformat(timespec='seconds')}",
             f"Сохранённых тестов: {len(results)}; разных исследований: {len(research_ids)}; "
             f"без research_id: {without_research}.",
             "Строки сопоставимы только при подтверждённых идентичности артефакта, среде и методике. "
             "Недостающие сведения обозначены как «неизвестно» и не объединяются.",
             "", "Последний завершённый замер для каждой конфигурации:", header]
    lines.extend(_model_overview_line(row) for row in successful)
    if not successful:
        lines.append("Завершённых замеров нет.")
    lines += ["", "Последние неуспешные попытки без более нового успешного замера:",
              "ID | Дата | Статус | Контекст | KV | MTP/Draft | Артефакт | "
              "Runtime/оборудование/драйвер | Методика/подпись | Причина"]
    for row in failed:
        config = row.get("config") or {}
        artifact = row.get("artifact") or {}
        environment = row.get("environment") or {}
        workload = row.get("workload") or {}
        mtp = (f"да/{config.get('draft', 'неизвестно')}" if config.get("mtp") is True else
               "нет" if config.get("mtp") is False else "неизвестно")
        env_label = "/".join(_model_cell(environment.get(key)) for key in
                             ("runtime_build", "hardware", "driver"))
        workload_label = "/".join((_model_cell(workload.get("method")),
                                    _model_cell(str(workload.get("signature"))[:12] if workload.get("signature") else None)))
        lines.append(" | ".join(_model_cell(value) for value in (
            row.get("id"), row.get("created_at"), row.get("status"), config.get("context"),
            config.get("kv_type"), mtp,
            str(artifact.get("digest"))[:12] if artifact.get("digest") else None,
            env_label, workload_label, row.get("error"))))
    if not failed:
        lines.append("Нет.")
    lines += ["", "Полная история всех сохранённых результатов:", reports_text(results, title)]
    return "\n".join(lines)
