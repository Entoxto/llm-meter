"""Open OpenCode on the active local server without editing user or project config."""
from __future__ import annotations

import json
import base64
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from uuid import uuid4
from urllib.request import Request, urlopen

from model_studio.platform.paths import data_dir
from model_studio.backends.ollama import is_context_profile
from . import browser_host


_web_lock = threading.RLock()
_owned_web: dict | None = None


def _stop_owned(owner: dict | None) -> None:
    if owner is None:
        return
    if owner.get("stopping"):
        owner["stopping"].set()
    if owner.get("browser_host"):
        owner["browser_host"].close()
    process = owner["process"]
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        stream = getattr(process, "stdout", None)
        if stream:
            stream.close()


def stop_web() -> None:
    """Stop only the web server started by this Studio process."""
    global _owned_web
    with _web_lock:
        owner, _owned_web = _owned_web, None
        _stop_owned(owner)


def status() -> dict | None:
    """Read cached companion status without waiting on the launch lock or network."""
    owner = _owned_web
    if owner is None:
        return None
    result = dict(owner["result"])
    host = owner.get("browser_host")
    if host:
        result["browser"] = host.snapshot()
    if owner["process"].poll() is not None:
        result["pid"] = 0
        result["browser"] = {"status": "stopped", "message": "OpenCode остановлен"}
    return result


def _disable_browser(root: Path, config: dict, url: str, password: str):
    """Remove the native plugin, even if an old session carries an allow rule."""
    disabled = dict(config, plugins=["-opencode.browser"])
    target = root / "opencode" / "opencode.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(disabled, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    auth = base64.b64encode(("opencode:" + password).encode()).decode()
    request = Request(url + "/api/location/reload", data=b"", method="POST",
                      headers={"Authorization": "Basic " + auth})
    with urlopen(request, timeout=10):
        pass


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
            "providers": {provider: definition},
            # The private web server starts before its browser host attaches.
            # Session-specific allow rules are added only after attachment.
            "permissions": [{"action": "browser", "resource": "*", "effect": "deny"}]}


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


def _web_url(process: subprocess.Popen, timeout: float = 12) -> str:
    lines: Queue[str] = Queue(maxsize=32)
    def read_output():
        try:
            for line in process.stdout:
                try:
                    lines.put_nowait(line)
                except Full:
                    pass
        except (OSError, ValueError):
            pass
        finally:
            try:
                lines.put_nowait("")
            except Full:
                pass
    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("OpenCode завершился до открытия веб-интерфейса.")
        try:
            line = lines.get(timeout=min(.2, max(.01, deadline - time.monotonic())))
        except Empty:
            continue
        match = re.search(r"^server listening on http://127\.0\.0\.1:(\d+)\b", line.strip())
        if match:
            port = int(match.group(1))
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return f"http://127.0.0.1:{port}"
    raise RuntimeError("Веб-интерфейс OpenCode не запустился вовремя.")


def launch(project: str, session: dict, configured: str = "", mode: str = "web") -> dict:
    if mode == "web":
        with _web_lock:
            return _launch(project, session, configured, mode)
    return _launch(project, session, configured, mode)


def _launch(project: str, session: dict, configured: str, mode: str) -> dict:
    global _owned_web
    if mode not in ("web", "tui"):
        raise ValueError("Неизвестный режим запуска OpenCode.")
    folder = Path(project).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("Папка проекта больше не существует. Выберите её заново.")
    binary = executable(configured)
    major = _major_version(binary)
    config = connection_config(session, major)
    host_available = major >= 2 and mode == "web" and browser_host.available()
    if major >= 2 and not host_available:
        config["plugins"] = ["-opencode.browser"]
    identity = (str(folder), str(binary), session.get("session_id"),
                json.dumps(config, sort_keys=True, ensure_ascii=False))
    if mode == "web":
        with _web_lock:
            if (_owned_web is not None and _owned_web["identity"] == identity
                    and _owned_web["process"].poll() is None):
                if not webbrowser.open(_owned_web["auth_url"]):
                    raise RuntimeError("Не удалось открыть браузер с OpenCode.")
                return status() or dict(_owned_web["result"])
    quiet = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    web_command = "serve" if major >= 2 else "web"
    help_args = [str(binary), web_command, "--help"] if mode == "web" else [str(binary), "--help"]
    help_result = subprocess.run(help_args, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace",
                                 timeout=10, creationflags=quiet)
    if help_result.returncode:
        raise RuntimeError("Не удалось проверить установленный OpenCode.")
    standalone = "--standalone" in help_result.stdout
    if mode == "web":
        if "--hostname" not in help_result.stdout or "--port" not in help_result.stdout:
            raise RuntimeError("Установленный OpenCode не поддерживает локальный веб-сервер. Обновите OpenCode.")
        args = [str(binary), web_command, "--hostname", "127.0.0.1", "--port", "0"]
    else:
        if major >= 2 and not standalone:
            raise RuntimeError("OpenCode V2 не поддерживает отдельный сервер в этой сборке.")
        args = [str(binary)]
        if standalone:
            args.append("--standalone")
        args.append(str(folder))
    environment = os.environ.copy()
    browser_owner_id = uuid4().hex
    if major >= 2:
        root = _write_v2_config(config)
        if host_available:
            config["plugins"] = [{"package": str(browser_host.bundle_root() / "gate"),
                "options": {"owner_id": browser_owner_id, "ready_file": str(root / "browser-ready.json")}}]
            (root / "opencode" / "opencode.json").write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        # V2 loads opencode.json from XDG_CONFIG_HOME. A private server is
        # essential: the shared background service has a different environment.
        environment["XDG_CONFIG_HOME"] = str(root)
        environment["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        environment.pop("OPENCODE_CONFIG_DIR", None)
        environment.pop("OPENCODE_CONFIG", None)
        environment.pop("OPENCODE_CONFIG_CONTENT", None)
    else:
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config, ensure_ascii=False)
    password = secrets.token_urlsafe(24) if mode == "web" and major >= 2 else None
    if password is not None:
        environment["OPENCODE_PASSWORD"] = password
    process = subprocess.Popen(args, cwd=folder, env=environment,
                               stdin=subprocess.DEVNULL if mode == "web" else None,
                               stdout=subprocess.PIPE if mode == "web" else None,
                               stderr=subprocess.STDOUT if mode == "web" else None,
                               text=mode == "web", encoding="utf-8" if mode == "web" else None,
                               errors="replace" if mode == "web" else None,
                               creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if mode == "web"
                                              else getattr(subprocess, "CREATE_NEW_CONSOLE", 0)))
    result = {"pid": process.pid, "project": str(folder), "executable": str(binary), "mode": mode}
    if mode == "web":
        owner = {"process": process, "stopping": threading.Event()}
        try:
            url = _web_url(process)
            browser_state = {"status": "unavailable", "message": "Браузер модели недоступен в этой сборке; browser-инструменты отключены."}
            if host_available:
                host = browser_host.BrowserHost(owner_id=browser_owner_id, ready_file=root / "browser-ready.json")
                owner["browser_host"] = host
                disabled = threading.Event()
                def host_failed():
                    if owner["stopping"].is_set() or disabled.is_set():
                        return
                    disabled.set()
                    try:
                        _disable_browser(root, config, url, password)
                    except Exception:
                        # Do not leave an owned server advertising a dead tool.
                        _stop_owned(owner)
                host.on_failure = host_failed
                try:
                    browser_state = host.start(url, password, str(folder))
                except Exception:
                    host_failed()
                    browser_state = {"status": "unavailable", "message": "Не удалось подключить браузер модели; browser-инструменты отключены."}
                if process.poll() is not None:
                    raise RuntimeError("Не удалось безопасно настроить браузер модели. OpenCode остановлен; модель продолжает работать.")
            result["browser"] = browser_state
            if password is not None:
                payload = json.dumps({"username": "opencode", "password": password},
                                     ensure_ascii=False, separators=(",", ":"))
                code = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
                auth_url = f"{url}/connect#{code}"
            else:
                auth_url = url
            if not webbrowser.open(auth_url):
                raise RuntimeError("Не удалось открыть браузер с OpenCode.")
            result["url"] = url
            with _web_lock:
                previous = _owned_web
                owner.update(identity=identity, url=url, auth_url=auth_url, result=dict(result))
                _owned_web = owner
                _stop_owned(previous)
        except Exception:
            _stop_owned(owner)
            raise
    return result
