"""Shared benchmark, telemetry and Ollama backend; standard library only."""
from __future__ import annotations

import csv
import ctypes
import datetime as dt
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import threading
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request

CONTEXT = 32768
TOKENS = 512
RUNS = 3
GIB = 1024 ** 3
PROMPT = (
    "Write a detailed technical essay of at least 2000 words about the history "
    "of computing, processors, memory, operating systems and networking. "
    "Explain concrete examples in continuous prose. Continue elaborating; "
    "do not summarize or conclude early."
)


class Cancelled(Exception):
    pass


class InterruptibleReader(io.RawIOBase):
    """Retry short socket waits without poisoning a socket.makefile buffer."""
    def __init__(self, sock, stop, cancelled, started):
        super().__init__()
        self.sock, self.stop, self.cancelled, self.started = sock, stop, cancelled, started
        # Keep the descriptor alive when HTTPConnection handles Connection: close.
        self.owner = sock.makefile("rb", buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        while True:
            if self.stop.is_set() or self.cancelled.is_set():
                raise Cancelled()
            if time.perf_counter() - self.started > 300:
                raise RuntimeError("Запрос превысил лимит 300 секунд.")
            try:
                return self.sock.recv_into(buffer)
            except socket.timeout:
                continue

    def close(self):
        self.owner.close()
        super().close()


class InterruptibleResponse(http.client.HTTPResponse):
    def __init__(self, sock, stop, cancelled, started, **kwargs):
        super().__init__(sock, **kwargs)
        reader = InterruptibleReader(sock, stop, cancelled, started)
        self.fp.close()
        self.fp = io.BufferedReader(reader)
        sock.settimeout(.25)


def normalize_host(value, default_port=11434):
    value = value.strip() or f"http://127.0.0.1:{default_port}"
    if "://" not in value:
        value = "http://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Адрес должен иметь вид http://127.0.0.1:11434")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Укажите адрес сервера без пароля, query и fragment.")
    host = parsed.hostname
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port or (443 if parsed.scheme == "https" else default_port)
    return f"{parsed.scheme}://{host}:{port}{parsed.path.rstrip('/')}"


class Client:
    backend = "ollama"
    label = "Ollama"
    default_port = 11434

    def __init__(self, host, context=CONTEXT):
        self.context = int(context)
        if not 1 <= self.context <= 2147483647:
            raise ValueError("Контекст должен быть целым числом от 1 до 2147483647.")
        self.host = normalize_host(host, self.default_port)
        self.local = urlsplit(self.host).hostname in ("localhost", "127.0.0.1", "::1")
        self._cancelled = threading.Event()
        self.model_info = {}
        # Local requests must not be routed through a system HTTP proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, payload=None, timeout=5, method=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.host + path, data=data,
                                     headers={"Content-Type": "application/json"}, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                body = response.read()
                result = json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{self.label} HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"Нет связи с {self.label} ({self.host}): {exc}") from exc
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(str(result["error"]))
        return result

    def list_models(self):
        return self.request("/api/tags").get("models", [])

    def loaded_models(self):
        return self.request("/api/ps", timeout=2).get("models", [])

    def prepare(self, model):
        version = self.request("/api/version").get("version")
        tag = next((m for m in self.list_models() if m.get("name") == model), {})
        info = self.request("/api/show", {"model": model}, timeout=15)
        if info.get("remote_host") or info.get("remote_model"):
            raise RuntimeError("Выберите локальную модель для теста GPU/RAM.")
        capabilities = info.get("capabilities", [])
        if capabilities and "completion" not in capabilities:
            raise RuntimeError("Модель не поддерживает генерацию текста (например, embeddings).")
        details = info.get("details", {})
        self.model_info = {"name": model, "quantization": details.get("quantization_level"),
                           "quantization_source": "/api/show.details.quantization_level",
                           "model_size_bytes": tag.get("size"), "model_size_source": "/api/tags (disk)",
                           "context_limit": None, "context_source": "/api/ps.context_length",
                           "offload": "Нет данных", "memory_source": "Ollama /api/ps"}
        return {"server_version": version, "ollama_version": version, "digest": tag.get("digest"),
                "model_details": details, "model_parameters": info.get("parameters")}

    def resident(self, model, result=None):
        loaded = selected_resident(self.loaded_models(), model)
        if loaded is None:
            raise RuntimeError("После генерации модель отсутствует в /api/ps; контекст не подтверждён.")
        memory = placement(loaded)
        share = memory["gpu_percent"]
        offload = "Нет данных" if share is None else f"{share:.1f}% памяти на GPU (учёт Ollama)"
        self.model_info = dict(self.model_info, context_limit=loaded.get("context_length"),
                               offload=offload, **memory)
        return loaded

    def cancel(self):
        self._cancelled.set()

    def stream(self, path, payload, stop, sse=False):
        """Shared cancellable transport: NDJSON or SSE, including multiline events."""
        if stop.is_set():
            raise Cancelled()
        parsed = urlsplit(self.host)
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = cls(parsed.hostname, parsed.port, timeout=5)
        started = time.perf_counter()
        self._cancelled.clear()
        conn.response_class = lambda sock, **kw: InterruptibleResponse(
            sock, stop, self._cancelled, started, **kw)
        try:
            conn.connect()
            if stop.is_set():
                raise Cancelled()
            conn.request("POST", parsed.path.rstrip("/") + path,
                         body=json.dumps(payload).encode("utf-8"),
                         headers={"Content-Type": "application/json"})
            with conn.getresponse() as response:
                if response.status != 200:
                    raise RuntimeError(f"{self.label} HTTP {response.status}: "
                                       + response.read().decode("utf-8", errors="replace"))
                data_lines = []
                for line in response:
                    if stop.is_set():
                        raise Cancelled()
                    line = line.decode("utf-8").rstrip("\r\n")
                    if sse:
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip(" "))
                        if line or not data_lines:
                            continue
                        line = "\n".join(data_lines)
                        data_lines.clear()
                        if line.strip() == "[DONE]":
                            yield {"_stream_done": True}, time.perf_counter() - started
                            return
                    if not line.strip():
                        continue
                    part = json.loads(line)
                    if part.get("error"):
                        raise RuntimeError(str(part["error"]))
                    yield part, time.perf_counter() - started
        except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
            if stop.is_set():
                raise Cancelled() from exc
            raise RuntimeError(f"Ошибка генерации: {exc}") from exc
        finally:
            conn.close()

    def generate(self, model, prompt, tokens, stop):
        payload = {"model": model, "prompt": prompt, "stream": True,
                   "keep_alive": "2m", "options": {"num_ctx": self.context,
                   "num_predict": tokens, "temperature": 0, "seed": 42}}
        first_output = None
        thinking_seen = False
        stream = self.stream("/api/generate", payload, stop)
        try:
            for part, elapsed in stream:
                thinking_seen |= bool(part.get("thinking"))
                if first_output is None and (part.get("response") or part.get("thinking")):
                    first_output = elapsed
                if part.get("done"):
                    count, duration = part.get("eval_count", 0), part.get("eval_duration", 0)
                    if count <= 0 or duration <= 0:
                        raise RuntimeError("Ollama не вернула корректные счётчики генерации.")
                    prompt_count = part.get("prompt_eval_count")
                    cached = part.get("prompt_eval_cached_count")
                    processed = None if prompt_count is None else max(0, prompt_count - (cached or 0))
                    prompt_seconds = seconds(part.get("prompt_eval_duration"), 1e9)
                    return {"tokens": count, "output_tokens": count,
                            "generation_seconds": duration / 1e9, "tokens_per_second": count * 1e9 / duration,
                            "prompt_tokens": prompt_count, "prompt_processed_tokens": processed,
                            "prompt_cached_tokens": cached, "prompt_seconds": prompt_seconds,
                            "prompt_tokens_per_second": rate(processed, prompt_seconds),
                            "prompt_rate_basis": "uncached" if cached is not None else "cache_count_unavailable",
                            "first_output_seconds": first_output, "ttft_seconds": first_output,
                            "load_seconds": seconds(part.get("load_duration"), 1e9),
                            "wall_seconds": elapsed, "thinking_seen": thinking_seen,
                            "metrics": {k: v for k, v in part.items() if k.endswith(("_count", "_duration"))
                                        or k in ("done_reason", "model")}}
            raise RuntimeError("Поток оборвался до итоговых счётчиков Ollama.")
        finally:
            stream.close()


def seconds(value, divisor):
    return value / divisor if isinstance(value, (int, float)) and value >= 0 else None


def rate(count, duration):
    return count / duration if count is not None and count > 0 and duration and duration > 0 else None


def placement(model):
    if "memory" in model:
        return model["memory"]
    total, vram = model.get("size"), model.get("size_vram")
    if not isinstance(total, (int, float)) or total <= 0 or not isinstance(vram, (int, float)):
        return {"vram_bytes": None, "ram_estimate_bytes": None, "gpu_percent": None}
    # Same accounting basis as `ollama ps`; this is not process working set.
    return {"vram_bytes": vram, "ram_estimate_bytes": max(0, total - vram),
            "gpu_percent": min(100, max(0, 100 * vram / total))}


def system_ram():
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in (
                "total_phys", "avail_phys", "total_page", "avail_page",
                "total_virtual", "avail_virtual", "avail_extended")]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return {"total_bytes": status.total_phys,
            "used_bytes": status.total_phys - status.avail_phys, "percent": status.load}


def number(value):
    try:
        return float(value.strip())
    except ValueError:
        return None


class Telemetry:
    def __init__(self, client):
        self.client = client
        self.smi = shutil.which("nvidia-smi")

    def snapshot(self):
        sample = {"time": dt.datetime.now().astimezone().isoformat(),
                  "monotonic": time.perf_counter(), "gpus": [], "ram": None,
                  "models": None, "errors": []}
        try:
            sample["models"] = self.client.loaded_models()
        except Exception as exc:
            sample["errors"].append(str(exc))
        if not self.client.local:
            sample["errors"].append("Удалённый сервер: локальная аппаратная телеметрия не используется.")
            return sample
        sample["ram"] = system_ram()
        if self.smi:
            try:
                result = subprocess.run(
                    [self.smi, "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
                     "--format=csv,noheader,nounits"], capture_output=True, text=True,
                    timeout=2, check=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                for row in csv.reader(result.stdout.splitlines()):
                    if len(row) != 5:
                        continue
                    used, total = number(row[3]), number(row[4])
                    sample["gpus"].append({"index": row[0].strip(), "name": row[1].strip(),
                        "utilization_percent": number(row[2]),
                        "used_bytes": None if used is None else used * 1024 ** 2,
                        "total_bytes": None if total is None else total * 1024 ** 2})
            except (OSError, subprocess.SubprocessError) as exc:
                sample["errors"].append(f"nvidia-smi: {exc}")
        else:
            sample["errors"].append("nvidia-smi не найден: загрузка GPU недоступна.")
        return sample


def selected_resident(models, model, digest=None):
    return next((m for m in models if m.get("name") == model or m.get("model") == model
                 or (digest and m.get("digest") == digest)), None)


def gpu_summary(samples):
    """Sample arithmetic mean/peak, per device; never sum GPU utilization percentages."""
    devices = {}
    for sample in samples:
        for gpu in sample.get("gpus", []):
            device = devices.setdefault(gpu["index"], {"name": gpu["name"], "values": [], "vram": []})
            if gpu.get("utilization_percent") is not None:
                device["values"].append(gpu["utilization_percent"])
            if gpu.get("used_bytes") is not None:
                device["vram"].append(gpu["used_bytes"])
    return [{"index": index, "name": d["name"], "sample_count": len(d["values"]),
             "mean_utilization_percent": statistics.mean(d["values"]) if d["values"] else None,
             "peak_utilization_percent": max(d["values"]) if d["values"] else None,
             "peak_vram_bytes": max(d["vram"]) if d["vram"] else None}
            for index, d in devices.items()]


def run_benchmark(client, model, emit, stop, output_dir, runs=RUNS, tokens=TOKENS):
    """Run in a worker thread. emit(event, data) must be thread safe."""
    report = {"schema_version": 2, "created_at": dt.datetime.now().astimezone().isoformat(),
              "backend": client.backend, "host": client.host, "model": model,
              "requested_context": client.context,
              "target_context": client.context,
              "requested_tokens_per_run": tokens, "requested_runs": runs,
              "prompt": PROMPT, "options": {"temperature": 0, "seed": 42},
              "thinking_mode": "model_default", "runs": [], "samples": [], "warnings": []}
    monitor_stop = threading.Event()
    telemetry = Telemetry(client)

    def sample():
        value = telemetry.snapshot()
        report["samples"].append(value)
        emit("telemetry", value)

    def monitor():
        while not monitor_stop.is_set():
            sample()
            monitor_stop.wait(1)

    def resident():
        loaded = client.resident(model)
        actual = loaded.get("context_length")
        report["model_info"] = dict(client.model_info)
        emit("model_info", report["model_info"])
        if actual != client.context:
            detail = (f"Перезапустите внешний llama-server с -c {client.context} -np 1 "
                      "или выберите режим «Запускать из LLM Meter»." if client.backend == "llama.cpp"
                      else "Выберите меньший контекст или проверьте настройки Ollama.")
            raise RuntimeError(f"Фактический context limit: {actual if actual is not None else 'неизвестен'}, "
                               f"выбран: {client.context}. {detail}")
        return loaded

    worker = None
    try:
        emit("status", "Проверка модели…")
        report.update(client.prepare(model))
        report["model_info"] = dict(client.model_info)
        emit("model_info", report["model_info"])
        if client.backend == "llama.cpp":
            resident()  # Validate external server before spending time on generation.
        before = client.loaded_models()
        report["models_before"] = before
        others = [m["name"] for m in before if m is not selected_resident(before, model, report.get("digest"))]
        if others:
            report["warnings"].append("В памяти были другие модели: " + ", ".join(others)
                                      + ". Они могут влиять на результат.")
        if stop.is_set():
            raise Cancelled()
        worker = threading.Thread(target=monitor, daemon=True)
        report["test_started_monotonic"] = time.perf_counter()
        worker.start()
        emit("status", "Загрузка и прогрев · до 16 токенов…")
        report["warmup"] = client.generate(model, "Warm up. " + PROMPT, 16, stop)
        report["placement_after_warmup"] = resident()
        for index in range(1, runs + 1):
            if stop.is_set():
                raise Cancelled()
            emit("status", f"Замер {index}/{runs} · до {tokens} токенов…")
            started = time.perf_counter()
            result = client.generate(model, f"Trial {index}. " + PROMPT, tokens, stop)
            result.update(index=index, started_monotonic=started, ended_monotonic=time.perf_counter())
            result["placement"] = resident()
            result["context_limit"] = client.model_info.get("context_limit")
            report["runs"].append(result)
            if result["tokens"] < tokens:
                report["warnings"].append(f"Замер {index}: модель завершила ответ на {result['tokens']} "
                                          f"токенах из {tokens}; короткий замер менее устойчив.")
            emit("run", result)
        speeds = [r["tokens_per_second"] for r in report["runs"]]
        report["summary"] = {"median_tokens_per_second": statistics.median(speeds),
            "aggregate_tokens_per_second": sum(r.get("generation_measured_tokens", r["tokens"]) for r in report["runs"]) /
                                           sum(r["generation_seconds"] for r in report["runs"]),
            "min_tokens_per_second": min(speeds), "max_tokens_per_second": max(speeds)}
        for key in ("prompt_tokens_per_second", "ttft_seconds"):
            values = [r[key] for r in report["runs"] if r.get(key) is not None]
            report["summary"]["median_" + key] = statistics.median(values) if values else None
        report["status"] = "completed"
    except Cancelled:
        report["status"] = "cancelled"
    except Exception as exc:
        if stop.is_set():
            report["status"] = "cancelled"
        else:
            report.update(status="error", error=str(exc))
    finally:
        report["test_ended_monotonic"] = time.perf_counter()
        monitor_stop.set()
        if worker:
            worker.join(timeout=6)
        # Monitor requests are bounded to 2s each; freeze samples before serializing.
        report["samples"] = list(report["samples"])
        report["gpu_summary"] = gpu_summary(report["samples"])
        report["gpu_summary_scope"] = "sample mean/peak, all GPUs on server host, warmup + measured requests"
        report["measured_gpu_summary"] = gpu_summary([
            s for s in report["samples"] if any(r["started_monotonic"] <= s.get("monotonic", 0)
            <= r["ended_monotonic"] for r in report["runs"])])
    try:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json"
        path = output_dir / filename
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["saved_to"] = str(path.resolve())
    except OSError as exc:
        report["warnings"].append(f"Не удалось сохранить отчёт: {exc}")
    emit("finished", report)
    return report
