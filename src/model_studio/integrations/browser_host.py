"""Own the official OpenCode browser companion; no browser tool implementation here."""
from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from model_studio.platform.paths import data_dir
from .opencode_browser_policy import grant_browser, revoke_browser


def bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "browser-host"
    return Path(__file__).resolve().parents[3] / "browser-host" / "dist" / "win-unpacked"


def available() -> bool:
    root = bundle_root()
    return (root / "ModelStudioBrowser.exe").is_file() and (root / "gate" / "index.mjs").is_file()


class BrowserHost:
    """Credential-free public status, pipe-bound lifetime and bounded startup."""

    def __init__(self, *, owner_id: str | None = None, ready_file: Path | None = None):
        self.process = None
        self._changed = threading.Event()
        self._lock = threading.Lock()
        self._status = {"status": "starting", "message": "Подключаем браузер модели…"}
        self._closed = False
        self._owner_id = owner_id or uuid4().hex
        self._ready_file = ready_file
        self._attached = set()
        self._file_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._failed = False
        self._url = ""
        self._password = ""
        self._project = ""
        self.on_failure = None

    def _request(self, path: str, payload: dict | None = None):
        auth = base64.b64encode(("opencode:" + self._password).encode()).decode()
        request = Request(self._url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"},
            method="PATCH" if payload is not None else "GET")
        with urlopen(request, timeout=10) as response:
            body = response.read()
        return json.loads(body) if body else None

    def _same_project(self, session: dict) -> bool:
        directory = (session.get("location") or {}).get("directory", "")
        return bool(directory) and Path(directory).resolve() == Path(self._project).resolve()

    def _set_permission(self, session_id: str, enabled: bool):
        if not session_id.startswith("ses"):
            return
        path = "/api/session/" + quote(session_id, safe="")
        response = self._request(path)
        session = response.get("data", response)
        if not self._same_project(session):
            return
        change = grant_browser(session, self._owner_id) if enabled else revoke_browser(session, self._owner_id)
        if change:
            self._request(path, change)
        with self._lock:
            if enabled:
                self._attached.add(session_id)
            else:
                self._attached.discard(session_id)
        self._publish_ready()

    def _publish_ready(self):
        if self._ready_file is None:
            return
        with self._file_lock:
            with self._lock:
                sessions = sorted(self._attached) if not self._closed and not self._failed else []
            value = {"owner_id": self._owner_id, "sessions": sessions, "updated_at": time.time()}
            self._ready_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._ready_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(value), encoding="utf-8")
            temporary.replace(self._ready_file)

    def _heartbeat(self):
        while not self._heartbeat_stop.wait(2):
            try:
                self._publish_ready()
            except OSError:
                self._fail()
                return

    def _fail(self):
        if self._closed or self._failed:
            return
        self._failed = True
        with self._lock:
            self._status = {"status": "unavailable", "message": "Браузер модели недоступен; инструменты браузера отключены."}
        try:
            self._publish_ready()
        except OSError:
            pass
        self._changed.set()
        if self.on_failure:
            self.on_failure()

    def _revoke_stale(self):
        cursor = None
        seen = set()
        while True:
            query = {"directory": self._project, "limit": 100}
            if cursor:
                query["cursor"] = cursor
            page = self._request("/api/session?" + urlencode(query))
            for session in page["data"]:
                if self._same_project(session):
                    change = revoke_browser(session)
                    if change:
                        self._request("/api/session/" + quote(session["id"], safe=""), change)
            cursor = page.get("cursor", {}).get("next")
            if not cursor or cursor in seen:
                return
            seen.add(cursor)

    def start(self, url: str, password: str, project: str, *, timeout: float = 30) -> dict:
        binary = bundle_root() / "ModelStudioBrowser.exe"
        if not binary.is_file():
            raise RuntimeError("Модуль браузера отсутствует в сборке Model Studio.")
        self._url, self._password, self._project = url, password, project
        self._publish_ready()
        self._revoke_stale()
        environment = os.environ.copy()
        # A host Electron variable inherited from another application must not
        # turn our sandboxed browser companion into a plain Node process.
        environment.pop("ELECTRON_RUN_AS_NODE", None)
        self.process = subprocess.Popen([str(binary)], cwd=binary.parent, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()
        try:
            payload = {"url": url, "password": password, "project": project,
                       "data_dir": str(data_dir() / "opencode" / "browser"), "parent_pid": os.getpid()}
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self._changed.wait(min(.2, max(0, deadline - time.monotonic())))
                self._changed.clear()
                state = self.snapshot()
                if state["status"] == "ready":
                    return state
                if state["status"] == "unavailable":
                    raise RuntimeError(state["message"])
            raise RuntimeError("Браузер модели не подтвердил подключение вовремя.")
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            process = self.process
            for line in process.stdout:
                if len(line) > 16384:
                    continue
                try:
                    value = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(value, dict):
                    continue
                kind = value.get("type")
                if self._failed:
                    continue
                if kind in ("attached", "detached"):
                    try:
                        self._set_permission(str(value.get("session_id") or ""), kind == "attached" and not self._closed)
                    except Exception:
                        kind = "error"
                # Never forward arbitrary child output: it may contain credentials.
                with self._lock:
                    if kind == "ready":
                        self._status = {"status": "ready", "message": "Браузер модели подключён"}
                    elif kind == "attached":
                        self._status = {"status": "ready", "message": "Браузер модели подключён"}
                    elif kind in ("error", "unavailable"):
                        self._status = {"status": "unavailable", "message": "Браузер модели недоступен; инструменты браузера отключены."}
                    elif kind == "reconnecting":
                        self._status = {"status": "reconnecting", "message": "Восстанавливаем подключение браузера…"}
                self._changed.set()
                if kind in ("error", "unavailable"):
                    self._fail()
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                if not self._closed:
                    self._status = {"status": "unavailable", "message": "Браузер модели остановлен; инструменты браузера отключены."}
            self._changed.set()
            self._fail()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._status)

    def close(self):
        self._closed = True
        self._heartbeat_stop.set()
        with self._lock:
            self._status = {"status": "stopped", "message": "Браузер модели остановлен"}
        try:
            self._publish_ready()
        except OSError:
            pass
        process = self.process
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        finally:
            if process.stdout:
                process.stdout.close()
            self.process = None
