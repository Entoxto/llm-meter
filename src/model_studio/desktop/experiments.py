"""Research workspace presentation; its draft never mutates the launch screen."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import threading

from PySide6.QtCore import QTimer

from model_studio.configuration import LaunchConfig


def research_input(value):
    """Detach QML values and preserve integral JS Numbers as Python integers.

    Number(text) produces a double even for a whole number. Fractional values,
    booleans and strings remain unchanged so domain validation can reject them.
    """
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    if isinstance(value, dict):
        return {key: research_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [research_input(item) for item in value]
    if type(value) is float and value.is_integer():
        return int(value)
    return value


class ExperimentPresentation:
    def __init__(self, studio):
        self.studio = studio
        self.revision = 0
        self.history_revision = 0
        self.cancel_preview = threading.Event()
        self.timer = QTimer(studio)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.preview)
        studio._values.update(researchDraft={}, researchPreview={"status": "empty"},
                              experimentResults=[], experimentJobs=[], experimentScope="model")

    @property
    def draft(self):
        return self.studio._values["researchDraft"]

    def ensure(self):
        if not self.draft:
            model = self.studio.selectedModel or next((m for m in self.studio.models
                if m.get("available") and m.get("testable", True)), {})
            if model:
                self.select_model(model["id"])

    def select_model(self, model_id):
        s = self.studio
        model = next((dict(m) for m in s.models if m["id"] == model_id), {})
        if not model:
            return
        try:
            from runtime_profiles import model_key
            projector = next((v for p, v in s.settings.get("model_projectors", {}).items()
                if model_key(p) == model_key(model.get("path", ""))), "") if model.get("backend") == "gguf" else ""
            model["mmproj_path"] = projector
            profile = s._profile(model)
            base = dict(context=32768, mtp=False, draft=2, reasoning="auto", reasoning_budget=None,
                        gpu_layers=99, kv_type="f16", vision=False)
            config = s._config(model, base, profile)
            previous = self.draft
            # Preserve explicit axes across a model change; unsupported selections are
            # explained by validation, never silently dropped.
            draft = dict(model_id=model_id, model_name=model.get("name", "Модель"),
                config=config.to_dict(), contexts=deepcopy(previous.get("contexts", [32768])),
                kv_types=deepcopy(previous.get("kv_types", ["f16"])),
                mtp_variants=deepcopy(previous.get("mtp_variants", [0])),
                checks=deepcopy(previous.get("checks", {"speed": True, "long_context": False})),
                runs=previous.get("runs", 1), skip_existing=previous.get("skip_existing", True),
                capabilities=list(config.capabilities), mtp_reason=profile.get("mtp_reason", "MTP недоступен в этой среде."),
                vision_available=bool(projector), projector=projector,
                runtime_id=profile.get("id", ""), runtime_options=[])
            if config.managed:
                draft["runtime_options"] = [{"text": "Обычный llama.cpp", "value": ""}] + [
                    {"text": p.get("name", key), "value": key}
                    for key, p in s.settings.get("runtime_profiles", {}).items()]
            s._update(researchDraft=draft, selectedResult={}, experimentScope="model")
            self.invalidate()
            self.load_results()
        except (ValueError, TypeError) as exc:
            s._update(researchPreview={"status": "error", "error": str(exc)}, error=str(exc))

    def invalidate(self):
        self.revision += 1
        self.cancel_preview.set()
        self.studio._update(researchPreview={"status": "pending"})
        self.timer.start()

    def set_option(self, key, value):
        value = research_input(value)
        draft = deepcopy(self.draft)
        if not draft:
            return
        try:
            if key in ("contexts", "kv_types", "mtp_variants", "checks", "runs", "skip_existing"):
                draft[key] = value
            elif key == "runtime_id":
                model = next(dict(m) for m in self.studio.models if m["id"] == draft["model_id"])
                settings = deepcopy(self.studio.settings)
                settings.setdefault("model_profiles", {})[model["path"]] = value
                # Remove alternate spellings of the same association.
                from runtime_profiles import model_key
                settings["model_profiles"] = {p: v for p, v in settings["model_profiles"].items()
                    if model_key(p) != model_key(model["path"])}
                if value:
                    settings["model_profiles"][model["path"]] = value
                profile = self.studio._profile(model, settings)
                from .controllers import normalize_profile
                extra, _, _ = normalize_profile(profile)
                draft["config"].update(executable=profile.get("executable", ""), extra_args=extra,
                    runtime_name=profile.get("name", "llama.cpp"), capabilities=profile.get("capabilities", []))
                draft.update(runtime_id=value, capabilities=profile.get("capabilities", []), mtp_reason=profile.get("mtp_reason", ""))
            elif key in ("reasoning", "reasoning_budget", "gpu_layers", "vision"):
                if key == "vision":
                    if value and not draft.get("vision_available"):
                        raise ValueError("Для этой модели не выбран модуль изображений.")
                    draft["config"]["mmproj"] = draft.get("projector", "") if value else ""
                else:
                    draft["config"][key] = value
                    if key == "reasoning":
                        draft["config"]["reasoning_budget"] = None
                    elif key == "reasoning_budget":
                        draft["config"].update(reasoning="on" if value else "auto", reasoning_budget=value or None)
            else:
                return
            self.studio._update(researchDraft=draft)
            self.invalidate()
        except (ValueError, TypeError, StopIteration) as exc:
            self.studio._update(error=str(exc))

    def copy_launch(self):
        try:
            config = self.studio._config()
            self.select_model(config.model_id)
            draft = deepcopy(self.draft)
            draft.update(config=config.to_dict(), contexts=[config.context], kv_types=[config.kv_type],
                         mtp_variants=[config.draft if config.mtp else 0])
            self.studio._update(researchDraft=draft, notice="Настройки запуска скопированы. Дальше они изменяются независимо.")
            self.invalidate()
        except (ValueError, TypeError) as exc:
            self.studio._update(error=str(exc))

    def plan(self):
        d = deepcopy(self.draft)
        config = LaunchConfig.from_dict(d["config"])
        plan = {k: d[k] for k in ("contexts", "kv_types", "mtp_variants", "checks", "runs", "skip_existing")}
        plan.update(scope="experiment", acknowledged_external=True)
        return config, plan

    def preview(self):
        if not self.draft or self.studio._closing:
            return
        self.timer.stop()
        revision = self.revision
        cancel = self.cancel_preview = threading.Event()
        try:
            config, plan = self.plan()
        except (ValueError, TypeError, KeyError) as exc:
            self.studio._update(researchPreview={"status": "error", "error": str(exc)})
            return
        def work():
            try:
                from model_studio.benchmarks.experiment import preview_experiment
                return {**preview_experiment(self.studio.store, config, plan, cancel), "status": "ready"}
            except Exception as exc:
                return {"status": "error", "error": str(exc)}
        def done(value):
            if revision == self.revision and not cancel.is_set():
                self.studio._update(researchPreview=value)
        self.studio._submit("experiment_preview:" + str(revision), work, done)

    def start(self):
        s = self.studio
        if s.busy or s.researchPreview.get("status") != "ready":
            return
        config, plan = self.plan()
        plan["preview_signature"] = s.researchPreview["signature"]
        revision = self.revision
        def begin():
            if revision != self.revision:
                s._update(error="Параметры изменились. Проверьте обновлённый план.")
                return
            from model_studio.benchmarks.research import run_research
            s._research_cancel = threading.Event()
            def done(job):
                s._update(research=job, experimentScope=job["id"])
                s._reload_history()
                self.load_results()
                self.invalidate()
            s._submit("research", lambda: run_research(s.core, s.store, config, plan,
                s._session_event, s._research_cancel), done, session=True)
        if s.session.get("status") == "ready" and s.researchPreview.get("checks_total", 0) > 0:
            s._confirmed_operation = begin
            s.confirmationRequested.emit("Исследование будет переключать конфигурации модели. Завершите запросы в чате и OpenCode. После исследования исходная сессия будет восстановлена.")
        else:
            begin()

    def set_scope(self, scope):
        self.studio._update(experimentScope=scope, selectedResult={})
        self.load_results()

    def load_results(self):
        s = self.studio
        self.history_revision += 1
        revision = self.history_revision
        model_id, scope = self.draft.get("model_id"), s.experimentScope
        models = deepcopy(s.models)
        def load():
            from model_studio.benchmarks.experiment import compose_evidence
            rows = [s._result_view(r, models, include_report=False) for r in s.store.results()]
            rows = [r for r in rows if (r.get("display_model_id") or r.get("model_id")) == model_id]
            jobs = [j for j in s.store.research_jobs() if (j.get("plan", {}).get("base_config") or {}).get("model_id") == model_id]
            if scope != "model":
                job = next((j for j in jobs if j["id"] == scope), {})
                if job.get("plan", {}).get("scope") == "experiment":
                    by_id = {r["id"]: r for r in rows}
                    projected = []
                    steps = list({step["key"]: step for step in job.get("completed_steps", [])}.values())
                    used = {step.get(k) for step in steps for k in ("speed_result_id", "long_result_id")}
                    for step in steps:
                        speed = by_id.get(step.get("speed_result_id"), {})
                        long = by_id.get(step.get("long_result_id"), {})
                        base = deepcopy(speed or long)
                        if not base:
                            base = s._result_view({"id": step["key"], "model_id": model_id,
                                "config": step.get("config", {}), "status": step.get("status", "error")}, models, False)
                        base.update(long_context=long.get("long_context", {}),
                            speed_result_id=step.get("speed_result_id"), long_result_id=step.get("long_result_id"),
                            speed_created_at=speed.get("created_at"), long_created_at=long.get("created_at"),
                            reused=step.get("reused", False), speed_status=step.get("speed_status"),
                            long_status=step.get("long_status"), error=step.get("error") or base.get("error"),
                            status=step.get("status", base.get("status")))
                        projected.append(base)
                    rows = projected + [r for r in rows if r.get("research_id") == scope and r["id"] not in used]
                else:
                    ids = {step.get("result_id") for step in job.get("completed_steps", [])}
                    rows = [r for r in rows if r.get("research_id") == scope or r["id"] in ids]
            else:
                rows = compose_evidence(rows)
                attached = {r.get("long_result_id") for r in rows if r.get("summary")}
                rows = [r for r in rows if r["id"] not in attached or r.get("summary")]
                rows = [{**r, "speed_created_at": r.get("created_at") if r.get("summary") else None,
                    "long_created_at": (r.get("long_context_provenance") or {}).get("source_created_at", r.get("created_at"))}
                    for r in rows]
            return {"rows": rows, "jobs": jobs}
        def done(value):
            if revision == self.history_revision:
                s._update(experimentResults=value["rows"], experimentJobs=value["jobs"])
        s._submit("experiment_history:" + str(revision), load, done)

    def apply_result(self, result_id):
        row = next((r for r in self.studio.experimentResults if r["id"] == result_id), {})
        self.apply_saved_result(row)

    def apply_saved_result(self, row):
        """Apply the same saved launch settings from research or a recommendation."""
        s = self.studio
        config = row.get("config") or {}
        if not config or row.get("status") not in ("completed", "complete"):
            s._update(error="Выберите завершённый замер.")
            return
        model_id = row.get("display_model_id") or row.get("model_id") or config.get("model_id")
        model = next((m for m in s.models if m["id"] == model_id), None)
        if not model or not model.get("available"):
            s._update(error="Модель этого замера больше не установлена.")
            return
        def apply():
            s._select(model)
            s._values["draft"].update({k: deepcopy(config[k]) for k in s.draft if k in config})
            s._values["draft"].update(vision=bool(config.get("mmproj")), reasoning_budget=config.get("reasoning_budget"))
            s._environment = None
            s._refresh_recommendations()
            s._capture_environment()
            s._update(page=0, selectedResult=s._result_view(row),
                notice="Настройки перенесены на экран запуска. Нажмите «Запустить модель», когда будете готовы.")
        if config.get("managed"):
            from runtime_profiles import model_key
            from .controllers import normalize_profile
            settings = deepcopy(s.settings)
            options = {"": {"executable": settings.get("server_exe", ""), "extra_args": []},
                       **settings.get("runtime_profiles", {})}
            profile_id = next((key for key, p in options.items() if p.get("executable") == config.get("executable")
                and normalize_profile(p)[0] == list(config.get("extra_args", []))), None)
            if profile_id is None:
                s._update(error="Среда этого замера больше не настроена. Добавьте её в настройках приложения.")
                return
            settings["model_profiles"] = {p: v for p, v in settings.get("model_profiles", {}).items()
                if model_key(p) != model_key(model.get("path", ""))}
            if profile_id:
                settings["model_profiles"][model["path"]] = profile_id
            if config.get("mmproj"):
                settings.setdefault("model_projectors", {})[model["path"]] = config["mmproj"]
            if config.get("host"):
                settings["managed_host"] = config["host"]
            def done(_):
                s._values["settings"] = settings
                apply()
            s._submit("apply_saved_result", lambda: s.store.save_settings(settings), done)
        else:
            apply()

    def clone(self):
        s = self.studio
        job = next((j for j in s.experimentJobs if j["id"] == s.experimentScope), None)
        plan = deepcopy(job.get("plan", {})) if job else {}
        config = plan.get("base_config") or s.selectedResult.get("config")
        if not config:
            return
        self.select_model(config.get("model_id"))
        draft = deepcopy(self.draft)
        draft["config"] = deepcopy(config)
        for key in ("contexts", "kv_types", "mtp_variants", "checks", "runs", "skip_existing"):
            if key in plan:
                draft[key] = plan[key]
        if not plan.get("scope") == "experiment":
            draft.update(contexts=plan.get("contexts", [config["context"]]), kv_types=[config.get("kv_type", "f16")],
                         mtp_variants=[config.get("draft", 2) if config.get("mtp") else 0])
        s._update(researchDraft=draft)
        self.invalidate()

    def report(self, scope, snapshot=None):
        """Read the complete saved set; never limit a report to loaded UI rows."""
        from model_studio.benchmarks.reports import reports_text, model_report_text, report_text, experiment_report_text
        s = self.studio
        snapshot = snapshot or self.report_snapshot()
        model_id = snapshot["draft"].get("model_id")
        rows = [s._result_view(r, snapshot["models"], include_report=False) for r in s.store.results()]
        rows = [r for r in rows if (r.get("display_model_id") or r.get("model_id")) == model_id]
        title = snapshot["draft"].get("model_name", "Модель")
        if scope == "selected":
            row = next((r for r in rows if r["id"] == snapshot["selected"].get("id")), None)
            if row is None:
                raise ValueError("Выберите замер для копирования.")
            long_id = snapshot["selected"].get("long_result_id")
            proof = next((r for r in rows if r["id"] == long_id), None)
            if proof and proof["id"] != row["id"]:
                return reports_text([row, proof], "Выбранная конфигурация: скорость и отдельная проверка длинного входа")
            return report_text(row)
        if scope == "model":
            return model_report_text(rows, title, include_history=False)
        if scope == "history" or (scope == "shown" and snapshot["scope"] == "model"):
            return reports_text(rows, "Полная история модели · " + title)
        job = next((j for j in s.store.research_jobs() if j["id"] == snapshot["scope"]), None)
        if not job:
            raise ValueError("Выберите исследование.")
        ids = {step.get(k) for step in job.get("completed_steps", [])
               for k in ("result_id", "speed_result_id", "long_result_id")}
        rows = [r for r in rows if r["id"] in ids or r.get("research_id") == job["id"]]
        return experiment_report_text(rows, title, job) if scope == "study" else reports_text(rows, title, job)

    def report_snapshot(self):
        return deepcopy({"draft": self.draft, "scope": self.studio.experimentScope,
                         "selected": self.studio.selectedResult, "models": self.studio.models})
