"""Explicit model directories, bounded GGUF metadata, and single-model deletion."""
from pathlib import Path
import datetime as dt
import json
import os
import shutil
import struct
import subprocess
import time


def testable_gguf(row):
    """A CLIP/mmproj sidecar is not a language model for the speed test."""
    return (row.get("architecture") or "").casefold() != "clip" and "mmproj" not in row["name"].casefold()


QUANTS = dict(enumerate(["F32", "F16", "Q4_0", "Q4_1"]))
QUANTS.update(dict(zip(range(7, 33), ["Q8_0", "Q5_0", "Q5_1", "Q2_K", "Q3_K_S", "Q3_K_M",
    "Q3_K_L", "Q4_K_S", "Q4_K_M", "Q5_K_S", "Q5_K_M", "Q6_K", "IQ2_XXS", "IQ2_XS",
    "Q2_K_S", "IQ3_XS", "IQ3_XXS", "IQ1_S", "IQ4_NL", "IQ3_S", "IQ3_M", "IQ2_S",
    "IQ2_M", "IQ4_XS", "IQ1_M", "BF16"])))
QUANTS.update({36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4_MOE", 39: "NVFP4", 40: "Q1_0", 41: "Q2_0"})
# Prism GGUF general.file_type values (distinct from GGML tensor type IDs).
QUANTS.update({141: "PQ2_0", 143: "PTQ1_0"})


def load_settings(path):
    data = None
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
    if data is None:
        bonsai = Path.home() / "Bonsai-demo"
        executables = [bonsai / "bin/cuda/llama-server.exe", bonsai / "bin/llama-server.exe"]
        data = {"model_dirs": [str(bonsai / "models")] if (bonsai / "models").is_dir() else [],
                "known_files": [], "server_exe": next((str(p) for p in executables if p.is_file()),
                                                       shutil.which("llama-server") or "")}
    # Kept separate so an older, already-open LLM Meter cannot erase new profile
    # registrations when it saves its in-memory settings on exit.
    local = path.with_name("runtime_profiles.local.json")
    if local.exists():
        try:
            profiles = json.loads(local.read_text(encoding="utf-8"))
            for key in ("runtime_profiles", "model_profiles"):
                if isinstance(profiles.get(key), dict):
                    data.setdefault(key, {}).update(profiles[key])
        except (OSError, ValueError, AttributeError):
            pass
    return data


def save_settings(path, settings):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def fingerprint(path):
    st = Path(path).stat()
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def gguf_metadata(path):
    """Read bounded GGUF header and tensor directory, never tensor weights."""
    limit = min(Path(path).stat().st_size, 128 * 1024 * 1024)
    with open(path, "rb") as f:
        def read(n):
            if n < 0 or f.tell() + n > limit:
                raise ValueError("GGUF metadata повреждены или превышают 128 MiB")
            result = f.read(n)
            if len(result) != n:
                raise ValueError("Неполный GGUF")
            return result

        def number(fmt):
            return struct.unpack("<" + fmt, read(struct.calcsize("<" + fmt)))[0]

        def skip(n):
            if n < 0 or f.tell() + n > limit:
                raise ValueError("Неполный GGUF metadata")
            f.seek(n, 1)

        def string(keep=True):
            n = number("Q")
            if keep:
                if n > 1024 * 1024:
                    raise ValueError("Слишком длинная строка metadata")
                return read(n).decode("utf-8", errors="replace")
            skip(n)

        formats = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?",
                   10: "Q", 11: "q", 12: "d"}

        def value(kind, keep=False):
            if kind in formats:
                return number(formats[kind])
            if kind == 8:
                return string(keep)
            if kind == 9:
                item, count = number("I"), number("Q")
                if item in formats:
                    skip(count * struct.calcsize("<" + formats[item]))
                elif item == 8 and count <= 2000000:
                    for _ in range(count):
                        string(False)
                else:
                    raise ValueError("Неподдерживаемый массив GGUF")
                return None
            raise ValueError("Неизвестный тип metadata GGUF")

        if read(4) != b"GGUF" or number("I") not in (2, 3):
            raise ValueError("Ожидается GGUF v2/v3")
        tensor_count = number("Q")
        if tensor_count > 100000:
            raise ValueError("Слишком много тензоров GGUF")
        count = number("Q")
        if count > 100000:
            raise ValueError("Слишком много metadata GGUF")
        result = {}
        for _ in range(count):
            key = string()
            keep = key in ("general.name", "general.architecture", "general.file_type") or key.endswith(
                (".nextn_predict_layers", ".block_count"))
            item = value(number("I"), keep)
            if keep:
                if key == "general.file_type" and not isinstance(item, int):
                    raise ValueError("Некорректный тип кванта GGUF")
                if key in ("general.name", "general.architecture") and not isinstance(item, str):
                    raise ValueError("Некорректная строка GGUF metadata")
                result[key] = item
        tensor_names = set()
        for _ in range(tensor_count):
            name = string()
            if len(name) > 4096:
                raise ValueError("Слишком длинное имя тензора GGUF")
            dimensions = number("I")
            if dimensions > 4:
                raise ValueError("Некорректная размерность тензора GGUF")
            skip(8 * dimensions + 4 + 8)  # shape, GGML type, data offset
            tensor_names.add(name)
        architecture = result.get("general.architecture")
        layers = result.get(f"{architecture}.nextn_predict_layers")
        blocks = result.get(f"{architecture}.block_count")
        evidence = {"architecture": architecture,
                    "nextn_predict_layers": layers if type(layers) is int else None,
                    "block_count": blocks if type(blocks) is int else None,
                    "head_block": None, "required_tensors_present": False}
        status = "unknown"
        if architecture == "qwen35":
            status = "unsupported"
            if type(layers) is int and type(blocks) is int and 0 < layers <= blocks:
                first_head = blocks - layers
                evidence["head_block"] = first_head
                for block in range(first_head, blocks):
                    prefix = f"blk.{block}."
                    required = {prefix + suffix for suffix in (
                        "attn_norm.weight", "post_attention_norm.weight", "attn_output.weight",
                        "attn_q_norm.weight", "attn_k_norm.weight", "ffn_gate.weight",
                        "ffn_down.weight", "ffn_up.weight", "nextn.eh_proj.weight",
                        "nextn.enorm.weight", "nextn.hnorm.weight")}
                    qkv = {prefix + suffix for suffix in
                           ("attn_q.weight", "attn_k.weight", "attn_v.weight")}
                    fused_qkv = prefix + "attn_qkv.weight"
                    if not required <= tensor_names or not (qkv <= tensor_names or fused_qkv in tensor_names):
                        break
                else:
                    evidence["required_tensors_present"] = True
                    status = "supported"
        result["mtp_model_status"] = status
        result["mtp_model_evidence"] = evidence
        quant = result.get("general.file_type")
        result["quant"] = QUANTS.get(quant, f"тип {quant}" if quant is not None else None)
        return result


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def scan_gguf(directories, known_files=()):
    paths, errors = set(), []
    for folder in directories:
        root = Path(folder)
        if not root.is_dir():
            errors.append(f"Папка недоступна: {root}")
            continue
        for current, dirs, files in os.walk(root, followlinks=False,
                                           onerror=lambda e: errors.append(str(e))):
            dirs[:] = [d for d in dirs if d not in (".cache", ".git") and not linked(Path(current) / d)]
            paths.update((Path(current) / f).absolute() for f in files
                         if f.lower().endswith(".gguf") and not linked(Path(current) / f))
    paths.update(Path(p).absolute() for p in known_files if Path(p).is_file() and not linked(Path(p)))
    rows = []
    for path in sorted(paths):
        if path.suffix.lower() != ".gguf":
            continue
        try:
            stamp = fingerprint(path)
            row = {"name": path.name, "path": str(path), "size": stamp[2], "fingerprint": stamp,
                   "modified": dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
            try:
                meta = gguf_metadata(path)
                row.update(quant=meta["quant"], architecture=meta.get("general.architecture"),
                           model_name=meta.get("general.name"),
                           mtp_model_status=meta["mtp_model_status"],
                           mtp_model_evidence=meta["mtp_model_evidence"])
            except (OSError, ValueError) as exc:
                row["error"] = str(exc)
            rows.append(row)
        except OSError as exc:
            errors.append(str(exc))
    return rows, errors


def validate_gguf(row, directories, known_files=()):
    path = Path(row["path"])
    if linked(path) or path.suffix.lower() != ".gguf" or not path.is_file():
        raise RuntimeError("Выбранный GGUF больше не является обычным файлом.")
    resolved = path.resolve()
    if not (any(resolved.is_relative_to(Path(d).resolve()) for d in directories)
            or resolved in [Path(p).resolve() for p in known_files]):
        raise RuntimeError("Файл больше не входит в список известных моделей.")
    if fingerprint(path) != tuple(row["fingerprint"]):
        raise RuntimeError("Файл изменился после обновления списка. Обновите список и подтвердите удаление заново.")
    return path


def delete_gguf(row, directories, known_files=()):
    path = validate_gguf(row, directories, known_files)
    before = shutil.disk_usage(path.parent).free
    path.unlink()  # Exact selected file only. Never remove its directory or companion files.
    return max(0, shutil.disk_usage(path.parent).free - before)


def local_model_processes(path):
    """Detect local llama processes using this exact model (including --mmproj)."""
    if os.name != "nt":
        raise RuntimeError("Проверка загруженных GGUF реализована для Windows; удаление заблокировано.")
    command = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.Name -match '^llama|prism|^server\\.exe$' } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Не удалось проверить запущенные llama-server; удаление отменено.")
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    rows = [rows] if isinstance(rows, dict) else rows
    import ctypes
    from ctypes import wintypes
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    matches = []
    target = Path(path).resolve()
    for row in rows:
        commandline = row.get("CommandLine")
        if not commandline:
            raise RuntimeError("Нет доступа к параметрам одного из llama-процессов; удаление заблокировано.")
        count = ctypes.c_int()
        argv = parse(commandline, ctypes.byref(count))
        if not argv:
            raise RuntimeError("Не удалось проверить параметры llama-процесса.")
        try:
            args = [argv[i] for i in range(count.value)]
        finally:
            ctypes.windll.kernel32.LocalFree(argv)
        candidates = [a.split("=", 1)[-1] for a in args if a.lower().endswith(".gguf")]
        if any(not Path(p).is_absolute() for p in candidates):
            raise RuntimeError("llama-процесс использует относительный путь GGUF. Остановите его перед удалением.")
        if any(Path(p).resolve() == target for p in candidates):
            matches.append(int(row["ProcessId"]))
    return matches


def ollama_loaded(client, row):
    return [m for m in client.loaded_models() if m.get("name") == row["name"]
            or (row.get("digest") and m.get("digest") == row["digest"])]


def delete_ollama(client, row, allow_unload):
    current = next((m for m in client.list_models() if m["name"] == row["name"]), None)
    if not current or current.get("digest") != row.get("digest") or current.get("size") != row.get("size"):
        raise RuntimeError("Модель изменилась. Обновите список и подтвердите удаление заново.")
    loaded = ollama_loaded(client, row)
    if loaded and not allow_unload:
        raise RuntimeError("Модель была загружена после подтверждения. Обновите список и повторите удаление.")
    for model in loaded:
        client.request("/api/generate", {"model": model["name"], "keep_alive": 0, "stream": False}, timeout=20)
    deadline = time.monotonic() + 15
    while ollama_loaded(client, row):
        if time.monotonic() > deadline:
            raise RuntimeError("Ollama не подтвердила выгрузку модели. Удаление отменено.")
        time.sleep(.25)
    client.request("/api/delete", {"model": row["name"]}, method="DELETE", timeout=30)
    if any(m["name"] == row["name"] for m in client.list_models()):
        raise RuntimeError("Ollama ещё показывает модель после удаления. Обновите список.")
