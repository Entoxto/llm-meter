"""Open OpenCode on the active local server without editing user or project config."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

from model_studio.platform.paths import data_dir
from model_studio.backends.ollama import is_context_profile


def executable(configured: str = "") -> Path:
    candidate = Path(configured) if configured else Path(shutil.which("opencode") or "")
    if candidate.is_file() and candidate.suffix.lower() == ".exe":
        return candidate.resolve()
    # npm Windows shims have no stable quoting contract for arbitrary paths.
    for relative in ("node_modules/@opencode/cli/bin/opencode.exe",
                     "node_modules/opencode-ai/node_modules/opencode-windows-x64/bin/opencode.exe"):
        native = candidate.parent / relative
        if native.is_file():
            return native.resolve()
    if os.name != "nt" and candidate.is_file():
        return candidate.resolve()
    raise ValueError("OpenCode не найден. Укажите путь к opencode.exe в настройках.")


def _session_details(session: dict) -> tuple[str, str, int]:
    if session.get("status") != "ready":
        raise ValueError("Сначала запустите модель.")
    # snapshot.model_id is the API model name; snapshot.config.model_id is
    # the persistent catalog UUID.
    model = str(session.get("model_id") or session.get("model") or "")
    host = str(session.get("host") or "").rstrip("/")
    if not model or not host.startswith(("http://", "https://")):
        raise ValueError("Сессия не содержит подтверждённого адреса или модели.")
    context = int(session.get("effective_context") or session.get("context") or 32768)
    if context < 2:
        raise ValueError("Контекст активной модели не определён.")
    return model, host if host.endswith("/v1") else host + "/v1", context


def connection_config(session: dict, major: int = 2) -> dict:
    model, base_url, context = _session_details(session)
    name = session.get("model_name") or session.get("model") or model
    output = max(1, min(4096, context // 4))
    if major < 2:
        return {"$schema": "https://opencode.ai/config.json",
                "model": "studio-local/" + model,
                "provider": {"studio-local": {
                    "npm": "@ai-sdk/openai-compatible", "name": "Модельная студия",
                    "options": {"baseURL": base_url},
                    "models": {model: {"name": name, "limit": {
                        "context": context, "output": output}}}}}}

    # V2 ignores OPENCODE_CONFIG_CONTENT and uses a plural providers map.
    # Ollama's native provider understands its model metadata and capabilities;
    # llama.cpp exposes OpenAI-compatible /v1 endpoints with an explicit model.
    provider = "ollama" if session.get("backend") == "ollama" else "studio-local"
    # A stable catalog key lets an existing OpenCode conversation follow the new
    # session profile on its next launch, instead of retaining a deleted UUID tag.
    catalog_model = str((session.get("config") or {}).get("model") or model) if (
        provider == "ollama" and is_context_profile(model)) else model
    model_info = {"name": name, "modelID": model, "limit": {"context": context, "output": output}}
    if provider == "ollama":
        capabilities = session.get("model_capabilities")
        if isinstance(capabilities, list):
            model_info["capabilities"] = {"tools": "tools" in capabilities,
                "input": ["text", "image"] if "vision" in capabilities else ["text"],
                "output": ["text"]}
        definition = {"package": "@opencode/ai/providers/openai-compatible",
                      "settings": {"baseURL": base_url}, "models": {catalog_model: model_info}}
        if catalog_model != model:
            definition["models"][model] = {"disabled": True}
    else:
        definition = {"name": "Модельная студия",
                      "package": "@opencode/ai/providers/openai-compatible",
                      "settings": {"baseURL": base_url}, "models": {model: model_info}}
    return {"$schema": "https://opencode.ai/config.json",
            "model": {"providerID": provider, "model": catalog_model},
            "providers": {provider: definition}}


def _major_version(binary: Path) -> int:
    result = subprocess.run([str(binary), "--version"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    match = re.search(r"\bv?(\d+)\.\d+\.\d+\b", result.stdout)
    if result.returncode or not match:
        raise RuntimeError("Не удалось определить версию установленного OpenCode.")
    return int(match.group(1))


def _write_v2_config(config: dict) -> Path:
    # Per-launch XDG root avoids races between concurrently opened projects.
    # Keep it while OpenCode runs: the private server can reload configuration.
    root = data_dir() / "opencode" / "launches" / uuid4().hex
    target = root / "opencode" / "opencode.json"
    target.parent.mkdir(parents=True, exist_ok=False)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return root


def launch(project: str, session: dict, configured: str = "") -> dict:
    folder = Path(project).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("Папка проекта больше не существует. Выберите её заново.")
    binary = executable(configured)
    major = _major_version(binary)
    config = connection_config(session, major)
    quiet = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    help_result = subprocess.run([str(binary), "--help"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace",
                                 timeout=10, creationflags=quiet)
    if help_result.returncode:
        raise RuntimeError("Не удалось проверить установленный OpenCode.")
    standalone = "--standalone" in help_result.stdout
    if major >= 2 and not standalone:
        raise RuntimeError("OpenCode V2 не поддерживает отдельный сервер в этой сборке.")
    args = [str(binary)]
    if standalone:
        args.append("--standalone")
    args.append(str(folder))
    environment = os.environ.copy()
    if major >= 2:
        root = _write_v2_config(config)
        # V2 loads opencode.json from XDG_CONFIG_HOME. A private server is
        # essential: the shared background service has a different environment.
        environment["XDG_CONFIG_HOME"] = str(root)
        environment["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        environment.pop("OPENCODE_CONFIG_DIR", None)
        environment.pop("OPENCODE_CONFIG", None)
        environment.pop("OPENCODE_CONFIG_CONTENT", None)
    else:
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config, ensure_ascii=False)
    process = subprocess.Popen(args, cwd=folder, env=environment,
                               creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return {"pid": process.pid, "project": str(folder), "executable": str(binary)}
