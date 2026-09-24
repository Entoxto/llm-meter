"""Bounded research over one exclusive session lease and saved measurements."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
from math import isfinite

from engine import PROMPT
from model_studio.configuration import LaunchConfig


_RUN_KEYS = ("index", "tokens", "output_tokens", "generation_seconds",
             "tokens_per_second", "prompt_tokens", "prompt_processed_tokens",
             "prompt_cached_tokens", "prompt_seconds", "ttft_seconds",
             "wall_seconds", "draft_n", "draft_n_accepted")


def _emit(emit, event: str, payload: dict) -> None:
    if emit:
        emit(event, payload)


def _validate_plan(config: LaunchConfig, plan: dict) -> tuple[list[int], int, int, int]:
    contexts = plan.get("contexts")
    if not isinstance(contexts, list) or not contexts or any(type(c) is not int or c < 256 for c in contexts):
        raise ValueError("plan.contexts must be a nonempty list of contexts >=256")
    contexts = list(dict.fromkeys(contexts))
    max_configs = plan.get("max_configs", 12)
    minutes = plan.get("budget_minutes")
    runs = plan.get("runs", 3)
    target = plan.get("target_context", max(contexts))
    if type(max_configs) is not int or not 1 <= max_configs <= 12:
        raise ValueError("max_configs must be between 1 and 12")
    if type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValueError("budget_minutes must be between 1 and 1440")
    if type(runs) is not int or not 1 <= runs <= 10:
        raise ValueError("runs must be between 1 and 10")
    if type(target) is not int or target < 256:
        raise ValueError("target_context must be >=256")
    if not plan.get("acknowledged_external", False):
        raise ValueError("External clients can compete with research; acknowledge them before starting")
    if config.mtp and "mtp" not in config.capabilities:
        raise ValueError("MTP is not a verified runtime capability")
    return contexts[:max_configs], minutes, runs, target


def plan_candidates(config, plan: dict) -> list[dict]:
    """Plan a small ordered sequence, never a context × feature product."""
    config = config if isinstance(config, LaunchConfig) else LaunchConfig.from_dict(config)
    contexts, _, _, target = _validate_plan(config, plan)
    cap = plan.get("max_configs", 12)
    candidates = [{"key": "baseline", "stage": "baseline", "config": config}]
    managed_llama = config.backend == "llama.cpp" and config.managed
    if managed_llama and "mtp" in config.capabilities:
        max_draft = plan.get("max_draft", config.draft)
        if type(max_draft) is not int or not 1 <= max_draft <= 32:
            raise ValueError("max_draft must be between 1 and 32")
        variants = [replace(config, mtp=False)] if config.mtp else []
        variants += [replace(config, mtp=True, draft=d) for d in (1, 2, 4) if d <= max_draft]
        for variant in variants:
            if variant == config:
                continue
            candidates.append({"key": f"accel:{int(variant.mtp)}:{variant.draft}",
                               "stage": "acceleration", "config": variant})
    context_candidates = [c for c in contexts if c != config.context]
    # Test the requested maximum even when the configuration budget is small.
    if target in context_candidates:
        context_candidates.remove(target)
        context_candidates.insert(0, target)
    kv_allowed = managed_llama and "kv-cache" in config.capabilities and (
        plan.get("memory_economy") is True or plan.get("fit_failure") is True)
    kv_types = [k for k in ("q8_0", "q4_0") if k != config.kv_type] if kv_allowed else []
    reserved_kv = min(len(kv_types), max(0, cap - 2))
    room_before_kv = max(1, cap - reserved_kv)
    # Keep the baseline and at least one context candidate if requested.
    reserve_context = 1 if context_candidates and room_before_kv > 1 else 0
    candidates = candidates[:max(1, room_before_kv - reserve_context)]
    for context in context_candidates:
        if len(candidates) >= room_before_kv:
            break
        candidates.append({"key": f"context:{context}", "stage": "context",
                           "config": replace(config, context=context)})
    for kv_type in kv_types:
        if len(candidates) >= cap:
            break
        candidates.append({"key": f"kv:{target}:{kv_type}", "stage": "kv",
                           "config": replace(config, context=target, kv_type=kv_type)})
    return candidates[:cap]


def _artifact(store, config: LaunchConfig) -> dict:
    row = next((r for r in store.models() if r["id"] == config.model_id), None)
    return {"id": config.model_id or None, "digest": row.get("digest") if row else None,
            "identity_verified": bool(row and row.get("identity_verified")),
            "backend": row.get("backend") if row else config.backend}


def _gpu_signature() -> tuple[str | None, str | None]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None, None
    try:
        found = subprocess.run([executable, "--query-gpu=name,memory.total,driver_version",
                                "--format=csv,noheader,nounits"], capture_output=True,
                               text=True, timeout=3, check=True)
        devices, versions = [], set()
        for row in csv.reader(found.stdout.splitlines()):
            if len(row) != 3:
                continue
            devices.append(f"{row[0].strip()}:{row[1].strip()}MiB")
            versions.add(row[2].strip())
        return (";".join(devices) or None, ",".join(sorted(versions)) or None)
    except (OSError, subprocess.SubprocessError):
        return None, None


def environment_snapshot(config, client=None, measured: dict | None = None) -> dict:
    """Bounded current runtime and hardware identity, safe for a background worker."""
    config = config if isinstance(config, LaunchConfig) else LaunchConfig.from_dict(config)
    measured = measured or {}
    host = config.host
    local = urlsplit(host).hostname in ("localhost", "127.0.0.1", "::1")
    hardware = driver = None
    if local:
        hardware, driver = _gpu_signature()
        if not hardware:
            processor = platform.processor() or platform.machine()
            hardware = f"CPU:{processor}:{platform.machine()}" if processor else None
            driver = "CPU" if hardware else None
    runtime_build = None
    version = measured.get("server_version") or measured.get("ollama_version")
    if config.managed and config.executable:
        try:
            stat = Path(config.executable).stat()
            runtime_build = f"exe:{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            pass
    elif client is not None or local:
        try:
            runtime_client = client if (client is not None and
                getattr(client, "backend", None) == config.backend and
                getattr(client, "host", "").rstrip("/") == host.rstrip("/")) else None
            if runtime_client is None:
                if config.backend == "ollama":
                    from engine import Client
                    runtime_client = Client(host, config.context)
                else:
                    from llama_cpp import LlamaCppClient
                    runtime_client = LlamaCppClient(host, context=config.context)
            if config.backend == "ollama":
                version = runtime_client.request("/api/version", timeout=2).get("version") or version
            else:
                response = runtime_client.request("/version", timeout=2)
                version = response.get("version") or response.get("build_info") or version
        except Exception:
            pass
        runtime_build = version
    supplied = measured.get("environment") if isinstance(measured.get("environment"), dict) else {}
    runtime_build = supplied.get("runtime_build") or runtime_build
    hardware = supplied.get("hardware") or hardware
    driver = supplied.get("driver") or driver
    if runtime_build is not None and not isinstance(runtime_build, str):
        runtime_build = json.dumps(runtime_build, sort_keys=True, default=str)
    return {"backend": config.backend, "host": host, "runtime_name": config.runtime_name,
            "runtime_build": runtime_build, "runtime_version": version,
            "hardware": hardware, "driver": driver,
            "verified": bool(local and runtime_build and hardware and driver),
            "sources": {"runtime_build": "executable stat" if config.managed else "runtime API",
                        "hardware": "nvidia-smi" if driver != "CPU" else "platform",
                        "driver": "nvidia-smi" if driver != "CPU" else "CPU"}}


def prepare_result(store, config, measured: dict, plan: dict | None = None, client=None) -> dict:
    """Convert a completed core benchmark into a compact, comparable snapshot.

    Used by both quick test and research. Missing provenance remains missing;
    this function never fills a numeric measurement from a guess.
    """
    config = config if isinstance(config, LaunchConfig) else LaunchConfig.from_dict(config)
    plan = plan or {}
    placement = measured.get("placement_after_warmup") or {}
    info = measured.get("model_info") or {}
    actual = placement.get("context_length") or info.get("context_limit")
    if actual is None and client is not None:
        try:
            placement = client.resident(measured.get("runtime_model_id") or config.model)
            actual = placement.get("context_length")
        except Exception:
            pass
    effective = config.to_dict()
    if actual is not None:
        effective["context"] = actual
    runs = [{k: run.get(k) for k in _RUN_KEYS if k in run} for run in measured.get("runs", [])]
    mtp_verified = not config.mtp or any((run.get("draft_n") or 0) > 0 for run in runs)
    memory = dict(placement.get("memory") or {k: info.get(k) for k in
                                              ("vram_bytes", "ram_estimate_bytes", "gpu_percent")})
    memory["source"] = info.get("memory_source") or placement.get("memory_source") or "runtime placement"
    memory["offload"] = info.get("offload")
    gpu_summary = list(measured.get("gpu_summary") or [])
    measured_gpu_summary = list(measured.get("measured_gpu_summary") or [])
    peaks = [item.get("peak_vram_bytes") for item in measured_gpu_summary
             if isinstance(item.get("peak_vram_bytes"), (int, float))]
    gpu_peak_bytes = max(peaks) if peaks else None
    environment = environment_snapshot(config, client, measured)
    supplied = plan.get("environment") if isinstance(plan.get("environment"), dict) else {}
    if supplied:
        for key in ("runtime_build", "hardware", "driver"):
            environment[key] = supplied.get(key) or environment[key]
        environment["verified"] = bool(supplied.get("verified") is True and all(
            environment.get(key) for key in ("runtime_build", "hardware", "driver")))
    prompt = measured.get("prompt") or PROMPT
    method = measured.get("method") or "legacy-short-v2"
    requested_runs = measured.get("requested_runs") or len(runs)
    requested_tokens = measured.get("requested_tokens_per_run") or 512
    options = measured.get("options") or {"temperature": 0, "seed": 42}
    warnings = list(measured.get("warnings") or [])
    blocking_reasons = []
    if measured.get("status") != "completed":
        blocking_reasons.append("Измерение не завершено")
    if len(runs) != requested_runs:
        blocking_reasons.append("Выполнены не все запрошенные прогоны")
    if actual != config.context:
        blocking_reasons.append("Фактический контекст не подтверждён")
    if not mtp_verified:
        blocking_reasons.append("MTP не подтверждён счётчиками runtime")
    for run in runs:
        if not isinstance(run.get("tokens"), (int, float)) or run["tokens"] < requested_tokens:
            blocking_reasons.append("Один из прогонов завершился до запрошенной длины ответа")
            break
        if (not isinstance(run.get("generation_seconds"), (int, float))
                or not isfinite(run["generation_seconds"]) or run["generation_seconds"] <= 0
                or not isinstance(run.get("tokens_per_second"), (int, float))
                or not isfinite(run["tokens_per_second"]) or run["tokens_per_second"] <= 0):
            blocking_reasons.append("Нет корректных данных о времени генерации")
            break
    before = measured.get("models_before")
    runtime_name = measured.get("runtime_model_id") or measured.get("model") or config.model
    digest = measured.get("digest")
    if isinstance(before, list) and any(
        (item.get("name") or item.get("model")) != runtime_name
        and not (digest and item.get("digest") == digest)
        for item in before if isinstance(item, dict)):
        blocking_reasons.append("Перед замером в памяти были другие модели")
    if any(any(mark in str(warning).casefold() for mark in
               ("в памяти были другие модели", "конкурирующ", "competing activity", "truncat", "усечен"))
           for warning in warnings):
        blocking_reasons.append("Runtime сообщил о конкурирующей нагрузке или усечении")
    signature = hashlib.sha256(json.dumps({"method": method,
        "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest(),
        "runs": requested_runs, "tokens": requested_tokens, "options": options},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"model_id": config.model_id or None, "status": measured.get("status", "error"),
            "created_at": measured.get("created_at") or datetime.now(timezone.utc).isoformat(),
            "session_id": measured.get("session_id"),
            "runtime_model_id": measured.get("runtime_model_id") or config.model,
            "config": config.to_dict(), "effective_config": effective,
            "effective_config_verified": actual == config.context and mtp_verified,
            "artifact": _artifact(store, config), "environment": environment,
            "workload": {"method": method, "signature": signature,
                         "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest(),
                         "runs_requested": requested_runs, "tokens_per_run": requested_tokens,
                         "options": options},
            "runs": runs, "summary": measured.get("summary"), "memory": memory,
            "gpu_summary": gpu_summary, "measured_gpu_summary": measured_gpu_summary,
            "gpu_summary_scope": measured.get("gpu_summary_scope"),
            "gpu_peak_bytes": gpu_peak_bytes,
            "gpu_peak_source": "measured_gpu_summary.peak_vram_bytes (per device)" if peaks else None,
            "model_info": {key: info.get(key) for key in
                           ("quantization", "quantization_source", "model_size_bytes",
                            "model_size_source", "context_limit", "context_source",
                            "offload", "memory_source", "runtime_profile", "mtp_status")
                           if key in info},
            "warnings": warnings, "blocking_reasons": blocking_reasons,
            "comparison_eligible": not blocking_reasons,
            **({"error": measured["error"]} if measured.get("error") else {})}


def _token_count(client, content: str, timeout: float) -> int | None:
    response = client.request("/tokenize", {"content": content, "with_pieces": False}, timeout=timeout)
    tokens = response.get("tokens") if isinstance(response, dict) else None
    if isinstance(tokens, list):
        return len(tokens)
    count = response.get("n_tokens") if isinstance(response, dict) else None
    return count if type(count) is int and count >= 0 else None


def _long_context_probe(client, model: str, context: int, stop: threading.Event,
                        deadline: float) -> dict:
    """Require tokenized long input, observed processing, output and exact context."""
    if getattr(client, "backend", None) != "llama.cpp":
        return {"validated": False, "reason": "Runtime tokenization proof unavailable"}
    reserve = min(128, max(16, context // 32))
    target = context - reserve - max(64, context // 100)
    if target < 256 or target > 131072:
        return {"validated": False, "reason": "Context outside bounded long-probe range"}
    phrase = "Подробное техническое описание процессора, памяти и вычисления. "
    repeats = max(1, target // 15)
    try:
        for _ in range(8):
            if stop.is_set() or time.monotonic() >= deadline:
                raise InterruptedError("Long-context probe cancelled")
            prompt = phrase * repeats + "\nПродолжите одним техническим предложением."
            count = _token_count(client, prompt, max(.1, min(30, deadline - time.monotonic())))
            if count is None or count <= 0:
                return {"validated": False, "reason": "Tokenizer did not return token count"}
            if int(target * .95) <= count <= context - reserve - 16:
                break
            repeats = max(1, int(repeats * target / count * .98))
        else:
            return {"validated": False, "reason": "Could not size long input safely"}
        if stop.is_set() or time.monotonic() >= deadline:
            raise InterruptedError("Long-context probe cancelled")
        measured = client.generate(model, prompt, reserve, stop)
        reported = measured.get("prompt_tokens")
        processed = measured.get("prompt_processed_tokens")
        cached = measured.get("prompt_cached_tokens")
        output = measured.get("output_tokens")
        actual = client.resident(model).get("context_length")
        accepted = processed + (cached or 0) if type(processed) is int else None
        valid = (actual == context and type(reported) is int and type(accepted) is int
                 and type(output) is int and output >= 8
                 and count <= reported <= context - reserve
                 and accepted >= count and reported + output <= context)
        return {"validated": bool(valid), "requested_context": context,
                "tokenized_input": count, "accepted_tokens": accepted,
                "reported_prompt_tokens": reported, "output_tokens": output,
                "observed_context": actual,
                "reason": "Long input accepted without observed truncation" if valid
                          else "Runtime counters did not prove full long input"}
    except InterruptedError:
        raise
    except Exception as exc:
        if stop.is_set():
            raise InterruptedError("Long-context probe cancelled") from exc
        return {"validated": False, "reason": f"Long-context probe unavailable: {exc}"}


def run_research(session, store, config, plan: dict, emit=None,
                 cancel: threading.Event | None = None) -> dict:
    """Run a bounded baseline, feature, context and optional KV sequence."""
    config = config if isinstance(config, LaunchConfig) else LaunchConfig.from_dict(config)
    plan = dict(plan)
    contexts, minutes, runs, target = _validate_plan(config, plan)
    plan["contexts"] = contexts
    plan["target_context"] = target
    candidates = plan_candidates(config, plan)
    resume_id = plan.pop("resume_job_id", None)
    initial = session.snapshot
    deadline = time.monotonic() + minutes * 60
    if resume_id:
        job = next((j for j in store.research_jobs() if j["id"] == resume_id), None)
        if job is None:
            raise KeyError(resume_id)
        if job["status"] == "completed":
            raise ValueError("Completed research cannot be resumed")
        saved_plan = job["plan"]
        if saved_plan.get("base_config") != config.to_dict():
            raise ValueError("Research config changed; start a new job")
        original_artifact = saved_plan.get("artifact") or {}
        current_artifact = _artifact(store, config)
        if not (original_artifact.get("identity_verified") and current_artifact.get("identity_verified")
                and original_artifact.get("digest") == current_artifact.get("digest")):
            raise ValueError("Artifact identity changed or is unverified; cannot reuse steps")
        old_env = saved_plan.get("baseline_environment") or {}
        new_env = environment_snapshot(config, session.client)
        if not (old_env.get("verified") and new_env.get("verified") and all(
                old_env.get(k) == new_env.get(k) for k in
                ("backend", "runtime_build", "hardware", "driver"))):
            raise ValueError("Runtime or hardware changed; start a new research job")
        plan = {**saved_plan, **plan}
        candidates = plan_candidates(config, plan)
        job = store.update_research(job["id"], {"status": "running", "stop_reason": None,
                                                   "restore_error": None, "error": None})
    else:
        baseline_env = environment_snapshot(config, session.client)
        job = store.create_research({**plan, "base_config": config.to_dict(),
                                     "artifact": _artifact(store, config),
                                     "baseline_environment": baseline_env,
                                     "initial_session": initial,
                                     "candidate_keys": [c["key"] for c in candidates]})
        job = store.update_research(job["id"], {"status": "running", "completed_steps": []})
    stop_watch = threading.Event()
    end_reason = None
    restore_error = None
    completed = {step.get("key"): step for step in job["completed_steps"]
                 if step.get("key") and step.get("status") == "completed"}
    speed_candidates = [step for step in completed.values() if step.get("stage") in ("baseline", "acceleration")
                        and step.get("comparison_eligible") and step.get("effective_config_verified")
                        and isinstance(step.get("speed"), (int, float))]
    long_proofs = {step.get("proof_key"): step.get("long_context") for step in completed.values()
                   if step.get("proof_key") and step.get("long_context")}
    attempted_keys: set[str] = set()
    try:
      with session.research_operation() as lease:
        def watch_cancel():
            nonlocal end_reason
            while not stop_watch.is_set():
                if cancel is not None and cancel.is_set():
                    end_reason = "cancelled"
                    session.cancel()
                    return
                if time.monotonic() >= deadline:
                    end_reason = "budget_exhausted"
                    session.cancel()
                    return
                stop_watch.wait(.05)
        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        try:
            _emit(emit, "research_started", {"job_id": job["id"], "total": len(candidates),
                                                "resumed": bool(resume_id)})
            index = 0
            while index < len(candidates):
                descriptor = candidates[index]
                index += 1
                key, stage = descriptor["key"], descriptor["stage"]
                if key in completed:
                    continue
                if (cancel is not None and cancel.is_set()) or lease.cancel_event.is_set() or time.monotonic() >= deadline:
                    end_reason = end_reason or ("budget_exhausted" if time.monotonic() >= deadline else "cancelled")
                    break
                if len(attempted_keys) >= plan.get("max_configs", 12):
                    end_reason = "configuration_limit"
                    break
                candidate = descriptor["config"]
                if stage in ("context", "kv") and speed_candidates:
                    fastest = max(speed_candidates, key=lambda item: item["speed"])
                    prior = LaunchConfig.from_dict(fastest["config"])
                    candidate = replace(candidate, mtp=prior.mtp, draft=prior.draft)
                attempted_keys.add(key)
                _emit(emit, "research_progress", {"job_id": job["id"], "index": index,
                                                    "total": len(candidates), "context": candidate.context,
                                                    "stage": stage})
                try:
                    started = lease.start(candidate)
                    if started.get("status") != "ready":
                        end_reason = "cancelled" if lease.cancel_event.is_set() else "start_failed"
                        break
                    proof_key = json.dumps({k: candidate.to_dict()[k] for k in
                                            ("model_id", "context", "mtp", "draft", "kv_type", "gpu_layers",
                                             "backend", "runtime_name")}, sort_keys=True)
                    needs_long = stage in ("context", "kv") or (stage == "baseline" and
                                  not any(c["stage"] == "context" for c in candidates))
                    if needs_long and proof_key in long_proofs:
                        long_context = long_proofs[proof_key]
                    elif needs_long and not lease.cancel_event.is_set() and time.monotonic() < deadline:
                        long_context = _long_context_probe(lease.client, lease.model_id,
                                                           candidate.context, lease.cancel_event, deadline)
                        long_proofs[proof_key] = long_context
                    else:
                        long_context = {"validated": False, "reason": "Long input is tested on context candidates"}
                    measured = lease.benchmark(runs=runs, tokens=512)
                    if measured is None:
                        raise RuntimeError("Benchmark returned no result")
                    result = prepare_result(store, candidate, measured, plan, lease.client)
                    result.update(research_id=job["id"], long_context=long_context)
                    saved = store.save_result(result)
                    speed = (saved.get("summary") or {}).get("median_tokens_per_second")
                    step = {"key": key, "stage": stage, "context": candidate.context,
                            "config": candidate.to_dict(), "result_id": saved["id"],
                            "status": saved["status"], "speed": speed,
                            "comparison_eligible": saved["comparison_eligible"],
                            "effective_config_verified": saved["effective_config_verified"],
                            "proof_key": proof_key, "long_context": long_context}
                    steps = job["completed_steps"] + [step]
                    job = store.update_research(job["id"], {"completed_steps": steps})
                    _emit(emit, "research_result", {"job_id": job["id"], "result": saved})
                    if saved["status"] == "completed":
                        completed[key] = step
                        if stage in ("baseline", "acceleration") and step["comparison_eligible"] \
                                and step["effective_config_verified"]:
                            speed_candidates.append(step)
                    if saved["status"] != "completed":
                        if lease.cancel_event.is_set():
                            end_reason = end_reason or "cancelled"
                            break
                        if "memory" in str(saved.get("error", "")).lower() or "oom" in str(saved.get("error", "")).lower():
                            plan["fit_failure"] = True
                            job = store.update_research(job["id"], {"plan": {**job["plan"], "fit_failure": True}})
                            fresh = plan_candidates(config, plan)
                            candidates = candidates[:index] + [c for c in fresh if c["key"] not in attempted_keys]
                            candidates = candidates[:plan.get("max_configs", 12)]
                            if index >= len(candidates):
                                end_reason = "fit_failure"
                                break
                            continue
                        end_reason = "measurement_failed"
                        break
                except InterruptedError:
                    end_reason = end_reason or ("budget_exhausted" if time.monotonic() >= deadline else "cancelled")
                    break
                except Exception as exc:
                    if not lease.cancel_event.is_set() and any(s in str(exc).lower() for s in ("memory", "oom")):
                        plan["fit_failure"] = True
                        job = store.update_research(job["id"], {"plan": {**job["plan"], "fit_failure": True},
                                                                "error": str(exc)})
                        fresh = plan_candidates(config, plan)
                        candidates = candidates[:index] + [c for c in fresh if c["key"] not in attempted_keys]
                        candidates = candidates[:plan.get("max_configs", 12)]
                        if index >= len(candidates):
                            end_reason = "fit_failure"
                            break
                        continue
                    end_reason = "cancelled" if lease.cancel_event.is_set() else "error"
                    job = store.update_research(job["id"], {"error": str(exc)})
                    break
        finally:
            stop_watch.set()
            watcher.join(timeout=1)
            # Restoration is a distinct phase after a cancelled request. The
            # session owner replaces its cancellation token under the lease.
            _emit(emit, "research_restoring", {"job_id": job["id"]})
            try:
                lease.begin_restoration()
                if initial.get("status") == "ready" and initial.get("config"):
                    restored = lease.start(LaunchConfig.from_dict(initial["config"]))
                    if restored.get("status") != "ready":
                        raise RuntimeError("Original session was not restored")
                else:
                    lease.unload()
            except Exception as exc:
                restore_error = str(exc)
                try:
                    # A second cancellation during restoration must not leave
                    # an owned server active in an indeterminate state.
                    lease.unload()
                except Exception as cleanup_exc:
                    restore_error += f"; cleanup: {cleanup_exc}"
    except Exception as exc:
        job = store.update_research(job["id"], {"status": "stopped",
                                                "stop_reason": "lease_or_storage_error", "error": str(exc)})
        raise
    status = "completed" if end_reason is None else ("cancelled" if end_reason == "cancelled" else "stopped")
    job = store.update_research(job["id"], {"status": status, "stop_reason": end_reason,
                                                "restore_error": restore_error})
    _emit(emit, "research_finished", job)
    return job


def resume_research(session, store, job_id: str, emit=None,
                    cancel: threading.Event | None = None) -> dict:
    """Explicitly retry incomplete steps of a compatible interrupted job."""
    job = next((row for row in store.research_jobs() if row["id"] == job_id), None)
    if job is None:
        raise KeyError(job_id)
    if job["status"] not in ("interrupted", "cancelled", "stopped", "failed"):
        raise ValueError("Only an interrupted or stopped research job can be resumed")
    plan = dict(job["plan"])
    if "base_config" not in plan:
        raise ValueError("This research has no resumable configuration snapshot")
    plan["resume_job_id"] = job_id
    return run_research(session, store, LaunchConfig.from_dict(plan["base_config"]),
                        plan, emit, cancel)
