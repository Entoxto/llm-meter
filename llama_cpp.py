"""llama-server backend. No architecture- or model-specific special cases."""
from pathlib import Path
import re
from urllib.parse import quote

from engine import Client, CONTEXT, seconds, rate


def log_memory(path, model_path):
    """Read the last matching model-load section, not sums across server restarts.

    Tensor buffer sizes are allocation estimates, not resident process RAM.
    A log from a different model is never used to populate these metrics.
    """
    if not path:
        return None
    with Path(path).open("rb") as source:
        source.seek(0, 2)
        size = source.tell()
        source.seek(max(0, size - 4 * 1024 * 1024))
        text = source.read().decode("utf-8", errors="replace")
    sections = list(re.finditer(r"^.*loaded meta data.*?from (.+?)\s*\(version.*$", text, re.M))
    if not sections or not model_path:
        raise ValueError("Лог не содержит проверяемой загрузки модели; память/offload не извлечены.")
    section = sections[-1]
    normalize = lambda p: p.strip().strip('"').replace("\\", "/").casefold()
    if normalize(section.group(1)) != normalize(model_path):
        raise ValueError("Последняя модель в логе не совпадает с model_path сервера.")
    text = text[section.start():]
    matches = re.findall(r"(?:load_tensors|llama_model_load)[^\n]*?([\w_]+)\s+model buffer size\s*=\s*([\d.]+)\s*(MiB|GiB)", text)
    gpu, cpu = [], []
    for device, value, unit in matches:
        amount = float(value) * 1024 ** (3 if unit == "GiB" else 2)
        if device.startswith("CPU") or device.endswith("_Host"):
            cpu.append(amount)
        elif device.startswith(("CUDA", "Vulkan", "ROCm", "Metal", "SYCL")):
            gpu.append(amount)
    offload = re.findall(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers to GPU", text)
    layers = tuple(map(int, offload[-1])) if offload else None
    quants = re.findall(r"(?:print_info|llm_load_print_meta)[^\n]*?file type\s*=\s*([^\r\n]+)", text)
    return {"vram_bytes": sum(gpu) if gpu else (0 if layers and layers[0] == 0 else None),
            "ram_estimate_bytes": sum(cpu) if cpu else None,
            "gpu_percent": None,  # A layer fraction must not masquerade as memory fraction.
            "offload": f"{layers[0]}/{layers[1]} слоёв на GPU" if layers else "Лог не сообщает offload",
            "offload_layers": layers[0] if layers else None,
            "total_layers": layers[1] if layers else None,
            "log_quantization": quants[-1].strip() if quants else None,
            "source": "llama-server startup log; model tensor buffers (excludes KV/compute)",
            "log_path": str(Path(path).resolve()), "log_mtime": Path(path).stat().st_mtime}


class LlamaCppClient(Client):
    backend = "llama.cpp"
    label = "llama-server"
    default_port = 8080

    def __init__(self, host, log_path="", context=CONTEXT):
        super().__init__(host, context)
        # Accept the common OpenAI-style base URL as well as the root URL.
        if self.host.endswith("/v1"):
            self.host = self.host[:-3]
        self.log_path = log_path
        self.selected_model = None
        self.metadata_warnings = []

    def list_models(self):
        data = self.request("/v1/models").get("data", [])
        return [dict(item, name=item["id"]) for item in data if item.get("id")]

    def loaded_models(self):
        rows = []
        for item in self.list_models():
            status = item.get("status") or {}
            if status and status.get("value") not in ("loaded", "sleeping"):
                continue
            meta = item.get("meta") or {}
            row = {"name": item["name"], "context_length": meta.get("n_ctx"),
                   "memory": {"vram_bytes": None, "ram_estimate_bytes": None, "gpu_percent": None}}
            if item["name"] == self.selected_model:
                row["context_length"] = self.model_info.get("context_limit")
                row["memory"] = {k: self.model_info.get(k) for k in row["memory"]}
            rows.append(row)
        return rows

    def metadata(self, model):
        warnings = []
        try:
            models = self.list_models()
        except RuntimeError as exc:
            models = []
            warnings.append("Автосписок недоступен: " + str(exc))
        item = next((m for m in models if m["name"] == model), None)
        if models and item is None:
            raise RuntimeError(f"Модель {model} отсутствует в /v1/models выбранного сервера.")
        item = item or {"name": model}
        meta = item.get("meta") or {}
        try:
            # Also routes correctly in llama-server multi-model/router mode.
            props = self.request("/props?model=" + quote(model, safe=""), timeout=5)
        except RuntimeError as exc:
            props = {}
            warnings.append("Свойства сервера недоступны: " + str(exc))
        context = props.get("default_generation_settings", {}).get("n_ctx") or meta.get("n_ctx")
        context_source = "/props.default_generation_settings.n_ctx" if props.get("default_generation_settings", {}).get("n_ctx") else "/v1/models.meta.n_ctx"
        if context is None:
            try:
                slots = self.request("/slots?model=" + quote(model, safe=""), timeout=2)
                limits = {s.get("n_ctx") for s in slots if isinstance(s, dict) and s.get("n_ctx")}
                if len(limits) == 1:
                    context = limits.pop()
                    context_source = "/slots.n_ctx (all slots agree)"
            except (RuntimeError, TypeError):
                pass
        quant = props.get("model_ftype") or meta.get("ftype")
        self.model_info = {"name": model, "quantization": quant,
            "quantization_source": "/props.model_ftype or /v1/models.meta.ftype",
            "model_size_bytes": meta.get("size"), "model_size_source": "/v1/models.meta.size (tensor bytes)",
            "model_path": props.get("model_path"), "context_limit": context,
            "context_source": context_source if context is not None else None,
            "training_context_limit": meta.get("n_ctx_train"),
            "vram_bytes": None, "ram_estimate_bytes": None, "gpu_percent": None,
            "offload": "API не сообщает; можно подключить лог запуска", "memory_source": "unavailable"}
        if self.log_path:
            try:
                memory = log_memory(self.log_path, props.get("model_path"))
                if memory:
                    self.model_info.update({k: v for k, v in memory.items() if k != "source"})
                    self.model_info["memory_source"] = memory["source"]
                    if not self.model_info["quantization"] and memory.get("log_quantization"):
                        self.model_info["quantization"] = memory["log_quantization"]
                        self.model_info["quantization_source"] = "startup log: file type"
                    warnings.append("Память/offload из лога: проверьте, что он относится к текущему запуску сервера.")
            except (OSError, ValueError) as exc:
                warnings.append(str(exc))
        self.metadata_warnings = warnings
        return props, meta

    def prepare(self, model):
        self.selected_model = model
        props, meta = self.metadata(model)
        return {"server_version": props.get("build_info"), "model_details": meta,
                "model_parameters": props.get("default_generation_settings"),
                "warnings": list(self.metadata_warnings),
                "prompt_cache_policy": "cache_prompt=false"}

    def resident(self, model, result=None):
        self.metadata(model)
        return {"name": model, "context_length": self.model_info["context_limit"],
                "memory": {k: self.model_info.get(k) for k in
                           ("vram_bytes", "ram_estimate_bytes", "gpu_percent")}}

    def generate(self, model, prompt, tokens, stop):
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "stream": True, "stream_options": {"include_usage": True},
                   "max_tokens": tokens, "temperature": 0, "seed": 42, "cache_prompt": False}
        first, thinking, finished = None, False, False
        timings, usage, fingerprint = {}, {}, None
        stream = self.stream("/v1/chat/completions", payload, stop, sse=True)
        elapsed = 0
        try:
            for part, elapsed in stream:
                if part.get("_stream_done"):
                    finished = True
                    break
                for choice in part.get("choices", []):
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    thinking |= bool(reasoning)
                    if first is None and (delta.get("content") or reasoning):
                        first = elapsed
                if part.get("timings"):
                    timings = part["timings"]
                if part.get("usage"):
                    usage = part["usage"]
                fingerprint = part.get("system_fingerprint", fingerprint)
        finally:
            stream.close()
        if not finished:
            raise RuntimeError("Поток llama-server оборвался до [DONE].")
        generated = timings.get("predicted_n")
        duration = seconds(timings.get("predicted_ms"), 1000)
        speed = rate(generated, duration)
        if speed is None:
            raise RuntimeError("llama-server не вернул timings.predicted_n/predicted_ms. "
                               "Используйте сборку с timings; при необходимости запустите с --perf.")
        processed = timings.get("prompt_n")
        cached = timings.get("cache_n", (usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
        total = usage.get("prompt_tokens")
        if total is None and processed is not None and cached is not None:
            total = processed + cached
        output = usage.get("completion_tokens", generated)
        prompt_seconds = seconds(timings.get("prompt_ms"), 1000)
        return {"tokens": output, "output_tokens": output, "generation_measured_tokens": generated,
                "generation_seconds": duration, "tokens_per_second": speed,
                "prompt_tokens": total, "prompt_processed_tokens": processed, "prompt_cached_tokens": cached,
                "prompt_seconds": prompt_seconds, "prompt_tokens_per_second": rate(processed, prompt_seconds),
                "prompt_rate_basis": "uncached", "first_output_seconds": first, "ttft_seconds": first,
                "load_seconds": None, "wall_seconds": elapsed, "thinking_seen": thinking,
                "metrics": {"timings": timings, "usage": usage, "system_fingerprint": fingerprint}}
