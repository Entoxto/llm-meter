"""Explicit Cartesian research with independent speed and long-input evidence."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import threading

from model_studio.configuration import LaunchConfig, effective_context_matches
from . import research as core
from .agent import METHOD, MIN_CONTEXT, scenario_seconds


MAX_CONFIGURATIONS = 256
_ENV_FIELDS = ("backend", "runtime_build", "hardware", "driver",
               "projector_digest", "projector_size_bytes", "projector_mtime_ns")


def _validated_plan(config: LaunchConfig, plan: dict) -> tuple[list[int], list[str], list[int], bool, bool, int]:
    if plan.get("scope") != "experiment":
        raise ValueError("Ожидается план исследования experiment")
    contexts = plan.get("contexts")
    kv_types = plan.get("kv_types")
    variants = plan.get("mtp_variants")
    if not isinstance(contexts, list) or not contexts or any(type(x) is not int or x < 256 for x in contexts):
        raise ValueError("Выберите хотя бы один контекст не меньше 256")
    if not isinstance(kv_types, list) or not kv_types or any(type(x) is not str for x in kv_types):
        raise ValueError("Выберите хотя бы один тип KV-кеша")
    if not isinstance(variants, list) or not variants or any(type(x) is not int or not 0 <= x <= 32 for x in variants):
        raise ValueError("Выберите значения Draft от 0 до 32")
    contexts, kv_types, variants = (list(dict.fromkeys(x)) for x in (contexts, kv_types, variants))
    checks = plan.get("checks")
    if not isinstance(checks, dict) or type(checks.get("speed")) is not bool or type(checks.get("long_context")) is not bool:
        raise ValueError("Укажите проверки скорости и длинного входа")
    speed, long = checks["speed"], checks["long_context"]
    if not speed and not long:
        raise ValueError("Выберите хотя бы одну проверку")
    if speed and config.backend != "llama.cpp":
        raise ValueError("Агентский сценарий пока доступен только для llama.cpp")
    if speed and any(context < MIN_CONTEXT for context in contexts):
        raise ValueError("Агентский сценарий использует историю 24K: выберите контексты 32K или больше")
    if long and any(context > 1_048_576 for context in contexts):
        raise ValueError("Проверка длинного входа поддерживает контекст до 1048576 токенов")
    if long and config.backend == "ollama":
        raise ValueError("Ollama: проверка длинного входа пока не реализована")
    runs = plan.get("runs", 1)
    if type(runs) is not int or not 1 <= runs <= 10:
        raise ValueError("Число прогонов должно быть от 1 до 10")
    if type(plan.get("skip_existing", True)) is not bool:
        raise ValueError("Повторное использование истории должно быть включено или выключено")
    if plan.get("acknowledged_external") is not True:
        raise ValueError("Подтвердите, что внешние клиенты могут конкурировать с исследованием")
    if len(contexts) * len(kv_types) * len(variants) > MAX_CONFIGURATIONS:
        raise ValueError(f"Исследование превышает предел {MAX_CONFIGURATIONS} конфигураций")
    if config.backend != "llama.cpp" or not config.managed:
        if kv_types != ["f16"] or variants != [0]:
            raise ValueError("Этот runtime поддерживает только f16 и MTP Выкл.")
    if any(k != "f16" for k in kv_types) and "kv-cache" not in config.capabilities:
        raise ValueError("Варианты KV требуют подтверждённой поддержки kv-cache")
    if any(v > 0 for v in variants) and "mtp" not in config.capabilities:
        raise ValueError("Варианты MTP требуют подтверждённой поддержки MTP")
    for k in kv_types:
        replace(config, kv_type=k, mtp=False)
    return contexts, kv_types, variants, speed, long, runs


def experiment_candidates(config: LaunchConfig | dict, plan: dict) -> list[dict]:
    config = config if isinstance(config, LaunchConfig) else LaunchConfig.from_dict(config)
    contexts, kv_types, variants, _, _, _ = _validated_plan(config, plan)
    return [{"key": f"experiment:{context}:{kv}:{variant}", "stage": "experiment",
             "config": replace(config, context=context, kv_type=kv, mtp=variant != 0,
                               draft=variant if variant else config.draft)}
            for context in contexts for kv in kv_types for variant in variants]


def _environment(config: LaunchConfig, plan: dict) -> dict:
    return core.environment_snapshot(config)


def _valid_long_proof(row: dict, candidate: LaunchConfig) -> bool:
    proof = row.get("long_context") or {}
    if proof.get("validated") is not True or type(proof.get("accepted_tokens")) is not int or proof["accepted_tokens"] <= 0:
        return False
    if candidate.mtp and not (type(proof.get("draft_n")) is int and proof["draft_n"] > 0):
        return False
    method = (row.get("workload") or {}).get("method")
    if method in ("legacy-short-v2", METHOD):
        return True
    if method != "studio-long-v1":
        return False
    count = proof.get("tokenized_input")
    accepted = proof["accepted_tokens"]
    reported = proof.get("reported_prompt_tokens")
    output = proof.get("output_tokens")
    return (proof.get("requested_context") == candidate.context
            and effective_context_matches(candidate, proof.get("observed_context"))
            and all(type(x) is int for x in (count, reported, output))
            and 0 < count <= reported <= accepted
            and output >= 8 and reported + output <= proof["observed_context"])


def _same_identity(row: dict, artifact: dict, env: dict, candidate: LaunchConfig) -> bool:
    old_artifact, old_env = row.get("artifact") or {}, row.get("environment") or {}
    if not (artifact.get("identity_verified") and artifact.get("digest")
            and old_artifact.get("identity_verified") is True
            and old_artifact.get("digest") == artifact["digest"]
            and old_artifact.get("projector") == artifact.get("projector")
            and env.get("verified") and old_env.get("verified") is True
            and all(old_env.get(k) == env.get(k) for k in _ENV_FIELDS)):
        return False
    expected = candidate.to_dict()
    requested, actual = row.get("config") or {}, row.get("effective_config") or {}
    ignored = {"capabilities", "runtime_name"} | ({"draft"} if not candidate.mtp else set())
    if any(requested.get(k) != v for k, v in expected.items() if k not in ignored):
        return False
    if any(actual.get(k) != v for k, v in expected.items() if k not in ignored | {"context"}):
        return False
    return (row.get("effective_config_verified") is True and
            effective_context_matches(candidate, actual.get("context")))


def _long_result(rows: list[dict], artifact: dict, env: dict, candidate: LaunchConfig) -> dict | None:
    for row in rows:
        if (row.get("status") == "completed" and _same_identity(row, artifact, env, candidate)
                and _valid_long_proof(row, candidate)):
            return row
    return None


def preview_experiment(store, config: LaunchConfig | dict, plan: dict,
                       cancel: threading.Event | None = None) -> dict:
    config = config if isinstance(config, LaunchConfig) else LaunchConfig.from_dict(config)
    _validated_plan(config, plan)
    config = core.verify_benchmark_model(store, config, cancel)
    candidates = experiment_candidates(config, plan)
    _, _, _, speed, long, runs = _validated_plan(config, plan)
    artifact = core._artifact(store, config)
    env = _environment(config, plan)
    resume_id = plan.get("resume_job_id")
    old = store.results(config.model_id) if plan.get("skip_existing", True) or resume_id else []
    if not plan.get("skip_existing", True):
        old = [row for row in old if row.get("research_id") == resume_id]
    speed_old = core._reusable_results(store, config, candidates, artifact, env, runs, results=old) if old else {}
    rows = []
    for item in candidates:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Просмотр исследования отменён")
        candidate = item["config"]
        long_old = _long_result(old, artifact, env, candidate) if long and candidate.backend == "llama.cpp" else None
        speed_result = speed_old.get(item["key"]) if speed else None
        speed_status = "history" if speed_result else "measure" if speed else "off"
        long_status = ("unavailable" if long and candidate.backend != "llama.cpp" else
                       "history" if long_old else "measure" if long else "off")
        rows.append({"key": item["key"], "config": candidate.to_dict(),
                     "context": candidate.context, "kv_type": candidate.kv_type,
                     "mtp": candidate.mtp, "draft": candidate.draft if candidate.mtp else 0,
                     "speed_status": speed_status, "long_status": long_status,
                     "speed_result_id": speed_result.get("id") if speed_result else None,
                     "long_result_id": long_old.get("id") if long_old else None,
                     "provenance": {"speed": speed_result.get("id") if speed_result else None,
                                    "long_context": long_old.get("id") if long_old else None}})
    known = sum(r["speed_status"] == "history" and r["long_status"] in ("history", "off", "unavailable")
                or r["long_status"] == "history" and r["speed_status"] == "off" for r in rows)
    supplement = sum((r["speed_status"] == "history" and r["long_status"] == "measure") or
                     (r["long_status"] == "history" and r["speed_status"] == "measure") for r in rows)
    new = sum(r["speed_status"] == "measure" and r["long_status"] in ("measure", "off", "unavailable") or
              r["long_status"] == "measure" and r["speed_status"] == "off" for r in rows)
    speed_checks = sum(r["speed_status"] == "measure" for r in rows)
    long_checks = sum(r["long_status"] == "measure" for r in rows)
    canonical_plan = {**plan, "skip_existing": plan.get("skip_existing", True),
                      "runs": plan.get("runs", 1), "benchmark_method": METHOD}
    signature_material = {"config": config.to_dict(), "plan": {k: v for k, v in canonical_plan.items()
                          if k not in ("preview_signature", "resume_job_id", "environment")},
                          "artifact": artifact, "environment": env,
                          "rows": [(r["key"], r["speed_status"], r["long_status"],
                                    r["speed_result_id"], r["long_result_id"]) for r in rows]}
    signature = hashlib.sha256(json.dumps(signature_material, sort_keys=True,
                             ensure_ascii=False, default=str).encode()).hexdigest()
    return {"rows": rows, "total": len(rows), "known": known, "supplement": supplement,
            "new": new, "speed_checks": speed_checks, "long_checks": long_checks,
            "checks_total": speed_checks + long_checks, "signature": signature,
            "base_config": config.to_dict(),
            "warnings": [] if env.get("verified") and artifact.get("identity_verified") else
                        ["Модель или среда не подтверждены: результаты истории нельзя использовать"],
            "artifact": artifact, "environment": env}


def _long_evidence_key(candidate: LaunchConfig, artifact: dict, env: dict) -> str:
    settings = candidate.to_dict()
    for field in ("capabilities", "runtime_name"):
        settings.pop(field, None)
    if not candidate.mtp:
        settings.pop("draft", None)
    return json.dumps([settings, artifact.get("digest"), artifact.get("projector"),
                       [env.get(field) for field in _ENV_FIELDS]], sort_keys=True, default=str)


def compose_evidence(results: list[dict]) -> list[dict]:
    """Project independent long proof onto speed rows without changing saved evidence."""
    # Index only validated proofs, in input order, preserving the first matching
    # source. Speed-only histories need no pairwise identity comparisons.
    proofs = {}
    for row in results:
        if row.get("status") != "completed" or (row.get("long_context") or {}).get("validated") is not True:
            continue
        try:
            candidate = LaunchConfig.from_dict(row["config"])
        except (KeyError, TypeError, ValueError):
            continue
        artifact, env = row.get("artifact") or {}, row.get("environment") or {}
        if _same_identity(row, artifact, env, candidate) and _valid_long_proof(row, candidate):
            proofs.setdefault(_long_evidence_key(candidate, artifact, env), row)
    projected = []
    for row in results:
        if ((row.get("workload") or {}).get("method") not in ("legacy-short-v2", METHOD)
                or row.get("comparison_eligible") is not True):
            projected.append(row)
            continue
        try:
            candidate = LaunchConfig.from_dict(row["config"])
        except (KeyError, TypeError, ValueError):
            projected.append(row)
            continue
        artifact, env = row.get("artifact") or {}, row.get("environment") or {}
        if _valid_long_proof(row, candidate):
            projected.append(row)
            continue
        proof = proofs.get(_long_evidence_key(candidate, artifact, env))
        if (proof is None or proof.get("id") == row.get("id")
                or not _same_identity(proof, artifact, env, candidate)):
            projected.append(row)
            continue
        projected.append({**row, "long_context": proof["long_context"],
                          "long_result_id": proof.get("id"),
                          "long_context_provenance": {"source_result_id": proof.get("id"),
                                                      "source_created_at": proof.get("created_at")}})
    return projected


def _job_counts(job: dict, total: int) -> dict:
    latest = {step["key"]: step for step in job.get("completed_steps", []) if step.get("key")}
    rows = list(latest.values())
    counts = {"total": total, "completed": sum(s.get("status") == "completed" for s in rows),
              "errors": sum(s.get("status") == "error" for s in rows),
              "pending": max(0, total - len(rows)),
              "history": sum(s.get("reused") is True for s in rows),
              "supplement": sum(s.get("reused") is False and
                                (s.get("speed_status") == "history" or s.get("long_status") == "history")
                                for s in rows),
              "new": sum(s.get("reused") is False and
                         s.get("speed_status") != "history" and s.get("long_status") != "history"
                         for s in rows),
              "speed_verified": sum(s.get("speed_verified") is True for s in rows),
              "long_verified": sum(s.get("long_verified") is True for s in rows)}
    return {"summary": counts, "errors_count": counts["errors"]}


def run_experiment(session, store, config, plan: dict, emit=None,
                   cancel: threading.Event | None = None) -> dict:
    config = config if isinstance(config, LaunchConfig) else LaunchConfig.from_dict(config)
    plan = dict(plan)
    plan.pop("tokens", None)
    plan["benchmark_method"] = METHOD
    resume_id = plan.pop("resume_job_id", None)
    plan.setdefault("skip_existing", True)
    requested_signature = plan.pop("preview_signature", None)
    before = preview_experiment(store, config,
                                {**plan, "resume_job_id": resume_id} if resume_id else plan, cancel)
    config = LaunchConfig.from_dict(before["base_config"])
    if requested_signature and requested_signature != before["signature"] and not resume_id:
        raise ValueError("План исследования изменился; обновите предварительный просмотр")
    preview = before
    initial = session.snapshot
    if resume_id:
        job = next((j for j in store.research_jobs() if j["id"] == resume_id), None)
        if job is None:
            raise KeyError(resume_id)
        saved = job["plan"]
        if saved.get("benchmark_method") != METHOD:
            raise ValueError("Методика исследования изменилась; создайте новый план. Старые результаты сохранены.")
        if saved.get("base_config") != config.to_dict() or saved.get("artifact") != preview["artifact"] or saved.get("baseline_environment") != preview["environment"]:
            raise ValueError("Модель или среда изменились; начните новое исследование")
        job = store.update_research(resume_id, {"status": "running", "stop_reason": None,
                                               "error": None, "restore_error": None})
    else:
        job = store.create_research({**plan, "base_config": config.to_dict(),
                                     "artifact": preview["artifact"],
                                     "baseline_environment": preview["environment"],
                                     "initial_session": initial,
                                     "candidate_keys": [r["key"] for r in preview["rows"]]})
        job = store.update_research(job["id"], {"status": "running", "completed_steps": []})
    old_rows = {r["id"]: r for r in store.results(config.model_id)}
    complete = {s["key"]: s for s in job["completed_steps"] if s.get("status") == "completed"}
    if not any(r["speed_status"] == "measure" or r["long_status"] == "measure"
               for r in preview["rows"] if r["key"] not in complete):
        core._emit(emit, "research_started", {"job_id": job["id"], "total": preview["total"],
                                               "scope": "experiment", "resumed": bool(resume_id),
                                               "speed_checks": 0, "long_checks": 0})
        for index, row in enumerate(preview["rows"], 1):
            if row["key"] in complete:
                continue
            speed_row = old_rows.get(row["speed_result_id"])
            long_row = old_rows.get(row["long_result_id"])
            step = {"key": row["key"], "stage": "experiment", "context": row["context"],
                    "config": row["config"], "result_id": row["speed_result_id"] or row["long_result_id"],
                    "speed_result_id": row["speed_result_id"], "long_result_id": row["long_result_id"],
                    "speed_status": row["speed_status"], "long_status": row["long_status"],
                    "status": "completed", "error": None, "reused": True,
                    "scenario_seconds": scenario_seconds(speed_row) if speed_row else None,
                    "comparison_eligible": bool(speed_row and speed_row.get("comparison_eligible")),
                    "effective_config_verified": bool(speed_row and speed_row.get("effective_config_verified")),
                    "long_context": (long_row or {}).get("long_context"),
                    "speed_verified": row["speed_status"] in ("off", "history"),
                    "long_verified": row["long_status"] in ("off", "history")}
            core._emit(emit, "research_progress", {"job_id": job["id"], "index": index,
                      "total": preview["total"], "context": row["context"],
                      "stage": "experiment", "reused": True})
            job = store.update_research(job["id"], {"completed_steps": job["completed_steps"] + [step]})
            if speed_row:
                core._emit(emit, "research_result", {"job_id": job["id"], "result": speed_row, "reused": True})
        job = store.update_research(job["id"], {"status": "completed", "stop_reason": None,
                                                **_job_counts(job, preview["total"])})
        core._emit(emit, "research_finished", job)
        return job
    stop_watch = threading.Event()
    end_reason = restore_error = None
    try:
      with session.research_operation() as lease:
        def watch_cancel():
            nonlocal end_reason
            while not stop_watch.is_set():
                if cancel is not None and cancel.is_set():
                    end_reason = "cancelled"
                    session.cancel()
                    return
                stop_watch.wait(.05)
        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        try:
            core._emit(emit, "research_started", {"job_id": job["id"], "total": preview["total"],
                                                   "scope": "experiment", "resumed": bool(resume_id),
                                                   "speed_checks": preview["speed_checks"],
                                                   "long_checks": preview["long_checks"]})
            for index, row in enumerate(preview["rows"], 1):
                if row["key"] in complete:
                    continue
                if (cancel is not None and cancel.is_set()) or lease.cancel_event.is_set():
                    end_reason = "cancelled"
                    break
                candidate = LaunchConfig.from_dict(row["config"])
                speed_id, long_id = row["speed_result_id"], row["long_result_id"]
                speed_row = old_rows.get(speed_id)
                long_row = old_rows.get(long_id)
                need_speed, need_long = row["speed_status"] == "measure", row["long_status"] == "measure"
                core._emit(emit, "research_progress", {"job_id": job["id"], "index": index,
                          "total": preview["total"], "context": candidate.context,
                          "stage": "experiment", "reused": not (need_speed or need_long),
                          "speed_status": row["speed_status"], "long_status": row["long_status"]})
                error = None
                if need_speed or need_long:
                    try:
                        started = lease.start(candidate)
                        if started.get("status") != "ready":
                            if lease.cancel_event.is_set():
                                raise InterruptedError("Experiment cancelled")
                            raise RuntimeError(started.get("error") or "Runtime did not start configuration")
                        if need_long:
                            core._emit(emit, "research_check", {"kind": "long_context", "config": candidate.to_dict()})
                            proof = core._long_context_probe(lease.client, lease.model_id,
                                                              candidate.context, lease.cancel_event, config=candidate)
                            if candidate.mtp and not (type(proof.get("draft_n")) is int
                                                      and proof["draft_n"] > 0):
                                proof = {**proof, "validated": False,
                                         "reason": "MTP was not confirmed by long-input runtime counters"}
                            observed_context = (lease.client.resident(lease.model_id) or {}).get("context_length")
                            long_row = store.save_result({"model_id": config.model_id, "research_id": job["id"],
                                "status": "completed", "config": candidate.to_dict(),
                                "effective_config": {**candidate.to_dict(), "context": observed_context},
                                "effective_config_verified": effective_context_matches(candidate, observed_context),
                                "artifact": preview["artifact"], "environment": preview["environment"],
                                "workload": {"method": "studio-long-v1"}, "long_context": proof,
                                "comparison_eligible": False})
                            long_id = long_row["id"]
                            old_rows[long_id] = long_row
                            core._emit(emit, "research_result", {"job_id": job["id"], "result": long_row})
                            if lease.cancel_event.is_set() or (cancel is not None and cancel.is_set()):
                                raise InterruptedError("Исследование отменено после проверки длинного входа")
                        if need_speed and not lease.cancel_event.is_set():
                            core._emit(emit, "research_check", {"kind": "speed", "config": candidate.to_dict()})
                            measured = lease.benchmark(runs=plan.get("runs", 1))
                            if measured is None:
                                raise RuntimeError("Benchmark returned no result")
                            speed_row = core.prepare_result(store, candidate, measured,
                                                            {k: v for k, v in plan.items() if k != "environment"},
                                                            lease.client)
                            speed_row.update(research_id=job["id"], long_context={"validated": False,
                                              "reason": "See independent long-context result"})
                            speed_row = store.save_result(speed_row)
                            speed_id = speed_row["id"]
                            old_rows[speed_id] = speed_row
                            core._emit(emit, "research_result", {"job_id": job["id"], "result": speed_row})
                    except InterruptedError:
                        end_reason = "cancelled"
                        break
                    except Exception as exc:
                        if lease.cancel_event.is_set():
                            end_reason = "cancelled"
                            break
                        error = str(exc)
                speed_ok = (row["speed_status"] == "off" or bool(speed_row and speed_row.get("comparison_eligible")))
                long_ok = (row["long_status"] in ("off", "unavailable") or bool(long_row and
                           _valid_long_proof(long_row, candidate)))
                if need_speed and speed_row and speed_row.get("status") != "completed":
                    error = error or speed_row.get("error") or "Speed check did not complete"
                if need_speed and not speed_ok:
                    error = error or "Speed result was not verified"
                if need_long and not long_ok:
                    error = error or ((long_row or {}).get("long_context") or {}).get("reason") or "Long input was not verified"
                step = {"key": row["key"], "stage": "experiment", "context": candidate.context,
                        "config": candidate.to_dict(), "result_id": speed_id or long_id,
                        "speed_result_id": speed_id, "long_result_id": long_id,
                        "speed_status": row["speed_status"], "long_status": row["long_status"],
                        "status": "completed" if not error else "error", "error": error,
                        "reused": not (need_speed or need_long),
                        "scenario_seconds": scenario_seconds(speed_row) if speed_row else None,
                        "comparison_eligible": bool(speed_row and speed_row.get("comparison_eligible")),
                        "effective_config_verified": bool(speed_row and speed_row.get("effective_config_verified")),
                        "long_context": (long_row or {}).get("long_context"),
                        "speed_verified": speed_ok, "long_verified": long_ok}
                job = store.update_research(job["id"], {"completed_steps": job["completed_steps"] + [step]})
        finally:
            stop_watch.set()
            watcher.join(timeout=1)
            core._emit(emit, "research_restoring", {"job_id": job["id"]})
            restore_error = core.restore_session(lease, initial)
    except Exception as exc:
        store.update_research(job["id"], {"status": "stopped", "stop_reason": "lease_or_storage_error",
                                          "error": str(exc)})
        raise
    job = store.update_research(job["id"], {"status": "cancelled" if end_reason else "completed",
                                           "stop_reason": end_reason, "restore_error": restore_error,
                                           **_job_counts(job, preview["total"])})
    core._emit(emit, "research_finished", job)
    return job
