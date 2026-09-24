"""Presentation bridge. Slow work stays in workers; snapshots stay in Qt."""
from __future__ import annotations

from dataclasses import replace
import datetime as dt
import json
import os
from pathlib import Path
import statistics
import threading
import time

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QFileDialog

from model_studio.catalog import Catalog
from model_studio.chat import ChatService
from model_studio.configuration import LaunchConfig
from model_studio.session import SessionController
from model_studio.storage import Store
from model_studio.integrations import opencode
from .workers import Workers


def qt_value(value):
    """Keep opaque file IDs exact across Python → QVariant → JavaScript."""
    if isinstance(value, dict):
        return {key: qt_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [qt_value(item) for item in value]
    if type(value) is int and abs(value) > 2**53 - 1:
        return str(value)
    return value


def normalize_profile(profile):
    """Import managed MTP flags into explicit settings, preserving unrelated args."""
    args = list(profile.get("extra_args") or [])
    remaining, mtp, draft = [], False, 2
    index = 0
    while index < len(args):
        value = args[index]
        if value in ("--spec-type", "--spec-draft-n-max") and index + 1 < len(args):
            if value == "--spec-type":
                if args[index + 1] != "draft-mtp":
                    raise ValueError("Этот тип speculative decoding пока не поддерживается: " + args[index + 1])
                mtp = True
            else:
                draft = int(args[index + 1])
            index += 2
        else:
            remaining.append(value)
            index += 1
    return remaining, mtp, draft


class Studio(QObject):
    changed = Signal()
    closeReady = Signal()
    confirmationRequested = Signal(str)

    def __init__(self, store: Store, paths: dict, legacy_root: Path, initialize=True, auto_import=False):
        super().__init__()
        self.store, self.paths, self.legacy_root = store, paths, legacy_root
        self._auto_import = auto_import
        self.catalog = Catalog(store)
        self.workers = Workers()
        self.core = SessionController(paths["logs"], self._session_event)
        self.chat_service = ChatService(store, self.core, self._session_event)
        self._values = dict(models=[], selectedModel={}, draft={"context": 32768, "mtp": False,
            "draft": 2, "reasoning": "auto", "kv_type": "f16", "gpu_layers": 99},
            session=self.core.snapshot, results=[], recommendations=[], telemetry={}, projects=[],
            selectedProject={}, conversations=[], messages=[], research={}, settings={},
            selectedResult={}, matchingResult={}, researchJobs=[], hasMoreResults=False,
            hasMoreConversations=False, busy=False, notice="", error="", page=0)
        self._pending = {}
        self._session_pending = False
        self._conversation_id = None
        self._conversation_revision = 0
        self._loading_conversation = False
        self._closing = False
        self._closed = False
        self._research_cancel = threading.Event()
        self._hash_cancel = threading.Event()
        self._telemetry_pending = False
        self._confirmed_operation = None
        self._environment = None
        self._environment_key = None
        self._hash_attempted = set()
        self._last_telemetry = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._pump)
        self._timer.start(40)
        if initialize:
            self._submit("load", self._load, self._loaded)

    def _get(name, qt_type, notify=changed):
        return Property(qt_type, lambda self: qt_value(self._values[name]), notify=notify)

    models = _get("models", "QVariantList")
    selectedModel = _get("selectedModel", "QVariantMap")
    draft = _get("draft", "QVariantMap")
    session = _get("session", "QVariantMap")
    results = _get("results", "QVariantList")
    recommendations = _get("recommendations", "QVariantList")
    telemetry = _get("telemetry", "QVariantMap")
    projects = _get("projects", "QVariantList")
    selectedProject = _get("selectedProject", "QVariantMap")
    conversations = _get("conversations", "QVariantList")
    messages = _get("messages", "QVariantList")
    research = _get("research", "QVariantMap")
    settings = _get("settings", "QVariantMap")
    selectedResult = _get("selectedResult", "QVariantMap")
    matchingResult = _get("matchingResult", "QVariantMap")
    researchJobs = _get("researchJobs", "QVariantList")
    hasMoreResults = _get("hasMoreResults", bool)
    hasMoreConversations = _get("hasMoreConversations", bool)
    busy = _get("busy", bool)
    notice = _get("notice", str)
    error = _get("error", str)

    @Property(int, notify=changed)
    def page(self):
        return self._values["page"]

    @page.setter
    def page(self, value):
        self._values["page"] = max(0, min(4, value))
        self.changed.emit()

    @Property(bool, notify=changed)
    def needsCloseConfirmation(self):
        return not self._closing and (self.busy or self.session.get("status") == "ready")

    def _update(self, **values):
        self._values.update(values)
        self.changed.emit()

    def _submit(self, name, function, done=None, session=False):
        if self._closing or name in self._pending:
            return False
        if session and self._session_pending:
            self._update(error="Дождитесь завершения текущей операции или остановите её.")
            return False
        self._pending[name] = (done, session)
        if session:
            self._session_pending = True
            self._hash_cancel.set()
            self._update(busy=True, error="")
        self.workers.submit(name, function, session)
        return True

    def _session_event(self, event, payload):
        self.workers.events.put((event, payload))

    def _pump(self):
        dirty = False
        for _ in range(500):
            if self.workers.events.empty():
                break
            event, data = self.workers.events.get()
            dirty = True
            if event in ("done", "failed"):
                callback, was_session = self._pending.pop(data["name"], (None, False))
                if was_session:
                    self._session_pending = any(v[1] for v in self._pending.values())
                    self._values["busy"] = self._session_pending
                if event == "failed":
                    self._values["error"] = data["message"]
                    if data["name"] == "messages:" + str(self._conversation_revision):
                        self._loading_conversation = False
                elif callback:
                    try:
                        callback(data["value"])
                    except Exception as exc:
                        self._values["error"] = str(exc)
                if data["name"] == "shutdown":
                    if event == "done":
                        self._closed = True
                        self.workers.shutdown()
                        self.closeReady.emit()
                    else:
                        self._closing = False
            elif event == "session":
                # core snapshot is authoritative; late queued states cannot regress it.
                snapshot = self.core.snapshot
                selected = next((m for m in self.models if m["id"] == (snapshot.get("config") or {}).get("model_id")), {})
                snapshot["model_name"] = selected.get("name") or Path(snapshot.get("model") or "").name
                self._values["session"] = snapshot
            elif event in ("text", "reasoning"):
                if self.messages and self.messages[-1].get("status") == "streaming":
                    key = "text" if event == "text" else "reasoning"
                    self._values["messages"][-1][key] += data.get("text", data.get(key, ""))
            elif event == "chat_started":
                self._conversation_id = data["conversation_id"]
                self._values["messages"] = data["messages"]
            elif event in ("chat_unsaved", "chat_save_error"):
                partial = data.get("result", data)
                if self.messages and self.messages[-1].get("role") == "assistant":
                    self._values["messages"][-1].update(text=partial.get("text", self.messages[-1]["text"]),
                        reasoning=partial.get("reasoning", ""), status="error")
                self._values["error"] = "Не удалось сохранить ответ: " + str(data.get("save_error", data.get("message", "")))
            elif event == "error":
                self._values["error"] = data.get("message", str(data))
            elif event == "status":
                self._values["notice"] = data.get("message", data.get("text", data.get("value", ""))) if isinstance(data, dict) else str(data)
            elif event == "telemetry":
                self._values["telemetry"] = self._telemetry_view(data)
            elif event == "research_started":
                self._values["research"] = {**data, "status": "running", "phase": "Начало исследования", "completed": 0, "progress": 0, "results": []}
            elif event == "research_progress":
                self._values["research"].update(data, status="running", phase="Проверяем конфигурацию", completed=data.get("index", 1) - 1,
                    progress=(data.get("index", 1) - 1) / max(1, data.get("total", 1)))
            elif event == "research_result":
                result = self._result_view(data["result"])
                self._values["research"].setdefault("results", []).append(result)
                self._values["results"].insert(0, result)
            elif event == "research_restoring":
                self._values["research"].update(status="running", phase="Восстанавливаем исходную сессию")
            elif event == "research_finished":
                self._values["research"].update(data)
                if data.get("restore_error"):
                    self._values["error"] = "Не удалось восстановить сессию: " + data["restore_error"]
            elif event == "progress":
                self._values["notice"] = "Измерение: " + str(data.get("completed", 0)) + " / " + str(data.get("total", 0))
        if dirty:
            self.changed.emit()
        if not self._closing and not self.busy and self.session.get("status") == "ready" and time.monotonic() - self._last_telemetry > 2:
            self._last_telemetry = time.monotonic()
            if "telemetry_poll" not in self._pending:
                client = self.core.client
                def measure():
                    from engine import Telemetry
                    if not client:
                        return {}
                    sample = Telemetry(client).snapshot()
                    sample["model_info"] = dict(getattr(client, "model_info", {}) or {})
                    return sample
                self._submit("telemetry_poll", measure, lambda v: self._update(telemetry=self._telemetry_view(v)))
            if "status_poll" not in self._pending:
                self._pending["status_poll"] = (None, False)
                self.workers.submit("status_poll", self.core.refresh_status, session=True)

    @staticmethod
    def _telemetry_view(sample):
        gpu = (sample.get("gpus") or [{}])[0]
        ram = sample.get("ram") or sample.get("system_ram") or {}
        info = sample.get("model_info") or {}
        return {"gpu_used_gb": round(gpu["used_bytes"] / 2**30, 1) if gpu.get("used_bytes") is not None else None,
                "gpu_total_gb": round(gpu["total_bytes"] / 2**30, 1) if gpu.get("total_bytes") is not None else None,
                "gpu_utilization": gpu.get("utilization_percent"), "ram_used_gb": round(ram["used_bytes"] / 2**30, 1) if ram.get("used_bytes") is not None else None,
                "ram_total_gb": round(ram["total_bytes"] / 2**30, 1) if ram.get("total_bytes") is not None else None,
                "model_vram_gb": round(info["vram_bytes"] / 2**30, 1) if info.get("vram_bytes") is not None else None,
                "model_ram_gb": round(info["ram_estimate_bytes"] / 2**30, 1) if info.get("ram_estimate_bytes") is not None else None,
                "offload": info.get("offload"), "model_memory_source": info.get("memory_source"),
                "source": "Сейчас · устройство / система", "raw": sample}

    def _load(self):
        settings = self.store.settings()
        if self._auto_import and not settings and (self.legacy_root / "settings.json").is_file():
            self.store.import_legacy(self.legacy_root)
            settings = self.store.settings()
        settings.setdefault("backend_hosts", {"Ollama": "http://127.0.0.1:11434", "llama.cpp": "http://127.0.0.1:8081"})
        settings["ollama_host"] = settings["backend_hosts"].get("Ollama", "http://127.0.0.1:11434")
        settings["llama_host"] = settings.get("managed_host") or settings["backend_hosts"].get("llama.cpp", "http://127.0.0.1:8081")
        settings["opencode_path"] = settings.get("opencode_exe", "")
        settings["reports_dir"] = str(self.paths["reports"])
        conversations, results = self.store.conversations(limit=200), self.store.results(limit=200)
        models = self.catalog.scan(settings)
        settings["ollama_available"] = self.catalog.connections.get("ollama")
        settings["llama_available"] = bool(settings.get("server_exe") and Path(settings["server_exe"]).is_file())
        return {"settings": settings, "models": models, "projects": self.store.projects(),
                "conversations": conversations, "results": results, "researchJobs": self.store.research_jobs(),
                "hasMoreResults": len(results) == 200, "hasMoreConversations": len(conversations) == 200}

    def _loaded(self, value):
        self._values.update(value)
        self._values["results"] = [self._result_view(r) for r in value["results"]]
        chosen = self.selectedModel.get("id")
        row = next((r for r in self.models if r["id"] == chosen), None)
        if not row:
            row = next((r for r in self.models if r.get("available") and r.get("testable", True)), {})
        self._select(row)
        if not self.selectedProject and self.projects:
            self._values["selectedProject"] = self.projects[0]
        self._values["notice"] = "; ".join(getattr(self.catalog, "errors", []) or [])
        self.changed.emit()

    def _reload_history(self):
        def load():
            return {"results": self.store.results(limit=200), "conversations": self.store.conversations(limit=200),
                    "researchJobs": self.store.research_jobs()}
        def done(value):
            value["results"] = [self._result_view(r) for r in value["results"]]
            self._values.update(value)
            self._values.update(hasMoreResults=len(value["results"]) == 200, hasMoreConversations=len(value["conversations"]) == 200)
            self._refresh_recommendations()
            self.changed.emit()
        self._submit("history", load, done)

    def _profile(self, model):
        from runtime_profiles import runtime_for
        return runtime_for(self.settings, model.get("path", ""), self.settings.get("server_exe", "")) if model.get("backend") == "gguf" else {}

    def _select(self, model):
        changed = self.selectedModel.get("id") != model.get("id")
        model = dict(model)
        if model:
            try:
                profile = self._profile(model)
                _, mtp, draft = normalize_profile(profile)
                model.update(capabilities=profile.get("capabilities", []), runtime_name=profile.get("name", "Ollama" if model.get("backend") == "ollama" else "Внешний llama.cpp"))
                if changed or self.selectedModel.get("runtime_name") != model.get("runtime_name"):
                    self._values["draft"].update(mtp=mtp, draft=draft)
                    if model.get("backend") == "llama.cpp" and model.get("context_limit"):
                        self._values["draft"]["context"] = int(model["context_limit"])
            except ValueError as exc:
                self._values["error"] = str(exc)
        self._values["selectedModel"] = model
        if changed:
            self._environment = None
        self._refresh_recommendations()
        self._capture_environment()
        if model and model.get("backend") == "gguf" and model.get("available") and not model.get("identity_verified") and model["id"] not in self._hash_attempted and not self.busy:
            self._hash_attempted.add(model["id"])
            self.verifyModel(model["id"])

    def _capture_environment(self):
        if self.busy:
            return
        try:
            from model_studio.benchmarks import environment_snapshot
            config = self._config()
        except (ImportError, TypeError, ValueError):
            return
        key = (config.model_id, config.backend, config.executable, config.host)
        if key == self._environment_key and self._environment is not None:
            return
        self._environment_key = key
        def done(value):
            if key == self._environment_key:
                self._environment = value
                self._values["settings"]["environment"] = str(value.get("hardware") or "Нет данных")
                self._refresh_recommendations()
                self.changed.emit()
            else:
                self._capture_environment()
        self._submit("environment", lambda: environment_snapshot(config), done)

    def _config(self):
        model = self.selectedModel
        if not model or not model.get("available") or not model.get("testable", True):
            raise ValueError("Выберите установленную модель.")
        draft = self.draft
        profile = self._profile(model)
        extra, _, _ = normalize_profile(profile)
        ollama = model["backend"] == "ollama"
        managed = model["backend"] == "gguf"
        return LaunchConfig(model=model.get("tag") or model.get("path") or model["name"],
            model_id=model["id"], backend="ollama" if ollama else "llama.cpp",
            context=int(draft["context"]), managed=managed,
            host=(model.get("host") if not managed else self.settings.get("managed_host")) or
                self.settings.get("backend_hosts", {}).get("Ollama" if ollama else "llama.cpp", "http://127.0.0.1:11434" if ollama else "http://127.0.0.1:8081"),
            executable=profile.get("executable", "") if managed else "",
            extra_args=tuple(extra), capabilities=tuple(profile.get("capabilities", [])),
            runtime_name=profile.get("name", "Ollama" if ollama else "llama.cpp"), mtp=bool(draft.get("mtp")) if managed else False,
            draft=int(draft.get("draft", 2)), reasoning=draft.get("reasoning", "auto"),
            gpu_layers=int(draft.get("gpu_layers", 99)) if managed else 99,
            kv_type=draft.get("kv_type", "f16") if managed else "f16")

    def _refresh_recommendations(self):
        self._values["matchingResult"] = {}
        try:
            from model_studio.benchmarks.recommendations import recommendations
            config = self._config().to_dict()
            digest = self.selectedModel.get("digest") if self.selectedModel.get("identity_verified") else None
            comparable = [r for r in self.results if digest and (r.get("artifact") or {}).get("digest") == digest]
            self._values["recommendations"] = recommendations(comparable, config, current_environment=self._environment)
            for result in comparable:
                effective = result.get("effective_config") or {}
                environment = result.get("environment") or {}
                if (result.get("model_id") == config.get("model_id") and result.get("effective_config_verified")
                    and result.get("comparison_eligible") and self._environment and self._environment.get("verified")
                    and all(environment.get(k) == self._environment.get(k) for k in ("backend", "runtime_build", "hardware", "driver"))
                    and all(effective.get(k) == v for k, v in config.items())
                    and result.get("status") == "completed"):
                    self._values["matchingResult"] = result
                    break
        except (ImportError, ValueError, TypeError):
            self._values["recommendations"] = []

    @Slot()
    def refresh(self):
        self._environment = None
        self._submit("load", self._load, self._loaded)

    @Slot(str)
    def selectModel(self, model_id):
        self._select(next((m for m in self.models if m["id"] == model_id), {}))
        self.changed.emit()

    @Slot(str, "QVariant")
    def setDraft(self, key, value):
        if key in self.draft:
            self._values["draft"][key] = value
            self._refresh_recommendations()
            self.changed.emit()

    @Slot()
    def startModel(self):
        try:
            config = self._config()
        except (ValueError, TypeError) as exc:
            self._update(error=str(exc)); return
        action = lambda: self._submit("start", lambda: self.core.start(config), session=True)
        self._apply_or_confirm(config, action)

    def _apply_or_confirm(self, config, action):
        if self.busy:
            self._update(error="Сначала завершите текущую операцию.")
            return
        if self.session.get("status") == "ready" and self.session.get("config") != config.to_dict():
            self._confirmed_operation = action
            self.confirmationRequested.emit("Будет остановлена текущая модель и запущена выбранная конфигурация. Подключение OpenCode и других клиентов прервётся.")
        else:
            action()

    @Slot(bool)
    def confirmOperation(self, accepted):
        action, self._confirmed_operation = self._confirmed_operation, None
        if accepted and action:
            action()

    @Slot(bool)
    def unloadModel(self, confirmed=False):
        if self.busy:
            if confirmed:
                self.cancel()
                if "unload" not in self._pending:
                    self._pending["unload"] = (None, True)
                    self.workers.submit("unload", self.core.unload, session=True)
                self._update(notice="Остановка операции и выгрузка модели…")
            return
        self._submit("unload", self.core.unload, session=True)

    @Slot()
    def cancel(self):
        self._research_cancel.set()
        self._hash_cancel.set()
        self.core.cancel()
        self._update(notice="Остановка операции…")

    @Slot()
    def importLegacy(self):
        folder = QFileDialog.getExistingDirectory(None, "Папка прежнего LLM Meter", str(self.legacy_root))
        if folder:
            self._submit("import", lambda: self.store.import_legacy(folder), lambda _: self.refresh())

    @Slot()
    def addModelFolder(self):
        folder = QFileDialog.getExistingDirectory(None, "Папка с моделями GGUF")
        if folder:
            settings = dict(self.settings)
            settings["model_dirs"] = list(dict.fromkeys([*settings.get("model_dirs", []), folder]))
            self.saveSettings(settings)

    @Slot()
    def addModelFile(self):
        path, _ = QFileDialog.getOpenFileName(None, "Модель GGUF", "", "GGUF (*.gguf)")
        if path:
            settings = dict(self.settings)
            settings["known_files"] = list(dict.fromkeys([*settings.get("known_files", []), path]))
            self.saveSettings(settings)

    @Slot(str)
    def deleteModel(self, model_id):
        active = (self.session.get("config") or {}).get("model_id")
        if model_id == active and self.session.get("status") == "ready":
            self._update(error="Сначала выгрузите эту модель."); return
        self._submit("delete_model", lambda: self.catalog.delete_model(model_id, dict(self.settings)), lambda _: self.refresh(), session=True)

    @Slot(str)
    def verifyModel(self, model_id):
        if self.busy:
            self._update(error="Проверка файла доступна после завершения текущей операции."); return
        if "hash" in self._pending:
            return
        cancel = self._hash_cancel = threading.Event()
        def verify():
            try:
                return self.catalog.hash_model(model_id, cancel)
            except InterruptedError:
                return None
        self._submit("hash", verify, lambda result: self.refresh() if result else None)

    @Slot()
    def openProjectFolder(self):
        folder = QFileDialog.getExistingDirectory(None, "Выберите папку проекта для OpenCode")
        if folder:
            def saved(value):
                self._update(selectedProject=value)
                self.refresh()
            self._submit("project", lambda: self.store.add_project(folder), saved)

    @Slot(str)
    def selectProject(self, project_id):
        self._update(selectedProject=next((p for p in self.projects if p["id"] == project_id), {}))

    @Slot()
    def openOpenCode(self):
        snapshot = dict(self.session)
        snapshot["model"] = snapshot.get("model_id") or snapshot.get("model")
        project = self.selectedProject.get("path", "")
        if not project:
            self._update(error="Выберите папку проекта для OpenCode."); return
        configured = self.settings.get("opencode_exe", "")
        def open_client():
            if self.core.snapshot.get("session_id") != snapshot.get("session_id") or self.core.snapshot.get("status") != "ready":
                raise RuntimeError("Сессия изменилась. Откройте OpenCode для текущей модели.")
            return opencode.launch(project, snapshot, configured)
        self._submit("opencode", open_client,
                     lambda _: self._update(notice="OpenCode открыт в выбранной папке."), session=True)

    @Slot()
    def newChat(self):
        if self.busy:
            self._update(error="Сначала остановите текущий ответ."); return
        self._conversation_id = None
        self._conversation_revision += 1
        self._loading_conversation = False
        self._update(messages=[], page=1)

    @Slot(str)
    def selectConversation(self, conversation_id):
        if self.busy:
            self._update(error="Сначала остановите текущий ответ."); return
        self._conversation_id = conversation_id
        self._conversation_revision += 1
        revision = self._conversation_revision
        self._loading_conversation = True
        self._update(messages=[])
        def done(value):
            if revision == self._conversation_revision:
                self._loading_conversation = False
                self._update(messages=value)
        self._submit("messages:" + str(revision), lambda: self.store.messages(conversation_id), done)

    @Slot(str)
    def deleteConversation(self, conversation_id):
        if self.busy:
            return
        def done(_):
            if self._conversation_id == conversation_id:
                self.newChat()
            self.refresh()
        self._submit("delete_chat", lambda: self.store.delete_conversation(conversation_id), done)

    @Slot(str)
    def sendMessage(self, text):
        text = text.strip()
        if not text:
            return
        if self._loading_conversation:
            self._update(error="Дождитесь загрузки диалога."); return
        if self.session.get("status") != "ready":
            self._update(error="Сначала запустите модель на странице «Запуск»."); return
        conversation_id = self._conversation_id
        max_tokens = min(2048, max(1, (self.session.get("context") or 32768) // 4))
        def done(value):
            self._conversation_id = value["conversation_id"]
            self._update(messages=value["messages"])
            self._reload_history()
        self._submit("chat", lambda: self.chat_service.send(conversation_id, text, max_tokens), done, session=True)

    def _result_view(self, result):
        result = dict(result)
        summary = result.get("summary") or {}
        config = result.get("config") or {}
        model = next((m for m in self.models if m["id"] == result.get("model_id")), {})
        result.update(context=config.get("context", result.get("requested_context")),
            model_name=model.get("name", Path(str(result.get("model") or "Модель")).name),
            speed=summary.get("median_tokens_per_second"), ttft=summary.get("median_ttft_seconds"),
            prompt_speed=summary.get("median_prompt_tokens_per_second"),
            vram_gb=round(result["gpu_peak_bytes"] / 2**30, 1) if result.get("gpu_peak_bytes") is not None else None,
            model_vram_gb=round((result.get("memory") or {})["vram_bytes"] / 2**30, 1) if (result.get("memory") or {}).get("vram_bytes") is not None else None)
        try:
            from model_studio.benchmarks.reports import report_text
            result["report"] = report_text(result)
        except ImportError:
            pass
        return result

    @Slot()
    def runBenchmark(self):
        try:
            config = self._config()
        except (ValueError, TypeError) as exc:
            self._update(error=str(exc)); return
        model = dict(self.selectedModel)
        def work():
            self.core.start(config)
            if self.core.snapshot["status"] != "ready":
                return None
            from model_studio.benchmarks.research import prepare_result
            result = prepare_result(self.store, config, self.core.benchmark(), client=self.core.client)
            return self.store.save_result(result)
        def done(result):
            if result:
                self._update(selectedResult=self._result_view(result), notice="Результат теста сохранён.")
            self._reload_history()
        self._apply_or_confirm(config, lambda: self._submit("benchmark", work, done, session=True))

    @Slot("QVariantMap")
    def runResearch(self, plan):
        try:
            from model_studio.benchmarks.research import run_research
            config = self._config()
        except (ValueError, TypeError, ImportError) as exc:
            self._update(error=str(exc)); return
        plan = dict(plan)
        maximum = int(plan.get("max_context", 131072))
        plan.setdefault("contexts", sorted({maximum, *(c for c in (32768, 65536, 98304, 102400, 131072) if c <= maximum)}))
        plan.setdefault("max_configs", 8)
        plan.setdefault("acknowledged_external", plan.get("external_use_acknowledged", False))
        plan.update(identity_verified=self.selectedModel.get("identity_verified", False), artifact_digest=self.selectedModel.get("digest"))
        self._research_cancel = threading.Event()
        self._submit("research", lambda: run_research(self.core, self.store, config, plan, self._session_event, self._research_cancel),
            lambda r: (self._update(research=r), self._reload_history()), session=True)

    @Slot(str)
    def resumeResearch(self, job_id):
        from model_studio.benchmarks.research import resume_research
        if self.busy:
            return
        def resume():
            self._research_cancel = threading.Event()
            self._submit("research", lambda: resume_research(self.core, self.store, job_id, self._session_event, self._research_cancel),
                lambda r: (self._update(research=r), self._reload_history()), session=True)
        self._confirmed_operation = resume
        self.confirmationRequested.emit("Завершите запросы OpenCode и других клиентов. Исследование будет переключать модель и настройки; затем восстановит исходную сессию. Продолжить?")

    @Slot(str)
    def selectResult(self, result_id):
        self._update(selectedResult=next((r for r in self.results if r["id"] == result_id), {}))

    @Slot(str)
    def applyRecommendation(self, key):
        rec = next((r for r in self.recommendations if r.get("key") == key and r.get("available")), None)
        if not rec:
            return
        result = next((r for r in self.results if r["id"] == rec["result_id"]), {})
        config = result.get("config", {})
        self._values["draft"].update({k: config[k] for k in self.draft if k in config})
        self._refresh_recommendations()
        self._update(selectedResult=result)

    @Slot(str)
    def copyText(self, text):
        QGuiApplication.clipboard().setText(text)
        self._update(notice="Скопировано в буфер обмена.")

    @Slot()
    def copyReport(self):
        if self.selectedResult:
            from model_studio.benchmarks.reports import report_text
            self.copyText(report_text(self.selectedResult))

    @Slot()
    def exportReport(self):
        if self.selectedResult:
            from model_studio.benchmarks.reports import export_result
            result = dict(self.selectedResult)
            self._submit("export", lambda: export_result(result, self.paths["reports"]), lambda p: self._update(notice="Отчёт сохранён: " + str(p)))

    @Slot()
    def openReports(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths["reports"])))

    @Slot("QVariantMap")
    def saveSettings(self, values):
        values = dict(values)
        merged = {**self.settings, **values}
        hosts = dict(merged.get("backend_hosts", {}))
        if "ollama_host" in values:
            hosts["Ollama"] = values["ollama_host"]
        if "llama_host" in values:
            hosts["llama.cpp"] = values["llama_host"]
            merged["managed_host"] = values["llama_host"]
        if "opencode_path" in values:
            merged["opencode_exe"] = values["opencode_path"]
        merged["backend_hosts"] = hosts
        for alias in ("ollama_host", "llama_host", "opencode_path", "reports_dir", "ollama_available", "llama_available", "environment"):
            merged.pop(alias, None)
        self._submit("settings", lambda: self.store.save_settings(merged), lambda _: self.refresh())

    @Slot()
    def chooseRuntime(self):
        path, _ = QFileDialog.getOpenFileName(None, "Исполняемый файл llama-server", "", "Приложения (*.exe)")
        if path:
            self.saveSettings({"server_exe": path})

    @Slot(str)
    def assignRuntime(self, profile_id):
        model = self.selectedModel
        if model.get("backend") != "gguf":
            return
        if profile_id and profile_id not in self.settings.get("runtime_profiles", {}):
            self._update(error="Профиль runtime не найден.")
            return
        from runtime_profiles import model_key
        key = model_key(model["path"])
        associations = {p: v for p, v in self.settings.get("model_profiles", {}).items() if model_key(p) != key}
        if profile_id:
            associations[model["path"]] = profile_id
        self.saveSettings({"model_profiles": associations})

    @Slot()
    def chooseOpenCode(self):
        path, _ = QFileDialog.getOpenFileName(None, "Исполняемый файл OpenCode", "", "Приложения (*.exe)")
        if path:
            self.saveSettings({"opencode_exe": path})

    @Slot()
    def loadMoreResults(self):
        offset = len(self.results)
        def done(rows):
            self._update(results=[*self.results, *(self._result_view(r) for r in rows)], hasMoreResults=len(rows) == 200)
        self._submit("more_results", lambda: self.store.results(limit=200, offset=offset), done)

    @Slot()
    def loadMoreConversations(self):
        offset = len(self.conversations)
        self._submit("more_chats", lambda: self.store.conversations(limit=200, offset=offset),
            lambda rows: self._update(conversations=[*self.conversations, *rows], hasMoreConversations=len(rows) == 200))

    @Slot()
    def backup(self):
        path = self.paths["backups"] / ("studio-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".db")
        self._submit("backup", lambda: self.store.backup(path), lambda p: self._update(notice="Резервная копия: " + str(p)))

    @Slot()
    def restoreBackup(self):
        if self.session.get("status") == "ready":
            self._update(error="Перед восстановлением выгрузите модель."); return
        if self.busy or self._pending:
            self._update(error="Для восстановления дождитесь завершения фоновых операций."); return
        path, _ = QFileDialog.getOpenFileName(None, "Восстановить резервную копию", str(self.paths["backups"]), "SQLite (*.db)")
        if path:
            self._submit("restore", lambda: self.store.restore_backup(path), lambda _: self.refresh(), session=True)

    @Slot()
    def clearError(self):
        self._update(error="")

    @Slot(bool, result=bool)
    def requestClose(self, confirmed=False):
        if self._closed:
            return True
        if self.needsCloseConfirmation and not confirmed:
            return False
        if self._closing:
            return False
        self.cancel()
        self._closing = True
        self._pending["shutdown"] = (None, True)
        self.workers.submit("shutdown", self.core.unload, session=True)
        return False
