"""Lifecycle of a llama-server created and owned by this application."""
from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

from engine import Cancelled
from model_studio.backends.llama_cpp import LlamaCppBackend
from model_studio.configuration import LaunchConfig, projector_identity
from model_studio.platform.windows_process import OwnedProcess


class ManagedRuntime:
    def __init__(self, logs_dir):
        self.logs_dir = Path(logs_dir)
        self.process: OwnedProcess | None = None
        self.log_path: Path | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            process.close()
            self.process = None

    def tail(self) -> str:
        if not self.log_path:
            return ""
        try:
            with self.log_path.open("rb") as source:
                source.seek(0, 2)
                source.seek(max(0, source.tell() - 4000))
                return source.read().decode("utf-8", errors="replace")[-2000:]
        except OSError:
            return ""

    def start(self, config: LaunchConfig, stop: threading.Event, emit, timeout: int = 120):
        executable = Path(config.executable).resolve()
        model = Path(config.model).resolve()
        if not executable.is_file():
            raise ValueError(f"llama-server executable does not exist: {executable}")
        if not model.is_file() or model.suffix.lower() != ".gguf":
            raise ValueError("Select an existing GGUF model file.")
        try:
            with model.open("rb") as source:
                source.read(1)
        except OSError as exc:
            raise ValueError("Selected GGUF model is not readable.") from exc
        projector = None
        if config.mmproj:
            try:
                projector = projector_identity(config.mmproj)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Vision projector is unavailable or unreadable: {exc}") from exc
            if Path(projector["path"]) == model:
                raise ValueError("Vision projector must differ from the model GGUF.")
        client = LlamaCppBackend(config.host, config.context)
        url = urlsplit(client.host)
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost") or url.path:
            raise ValueError("Managed server requires http://127.0.0.1:port without a path.")
        if stop.is_set():
            raise Cancelled()
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", url.port))
            except OSError as exc:
                raise RuntimeError(f"Port {url.port} is occupied; select another port.") from exc
        args = [str(executable), "-m", str(model), "-c", str(config.context), "-np", "1",
                "-ngl", str(config.gpu_layers), "--host", "127.0.0.1", "--port", str(url.port),
                "-lv", "4"]
        if config.kv_type != "f16":
            args += ["--cache-type-k", config.kv_type, "--cache-type-v", config.kv_type]
        if config.reasoning != "auto":
            args += ["--reasoning", config.reasoning]
        if config.mtp:
            args += ["--spec-type", "draft-mtp", "--spec-draft-n-max", str(config.draft)]
        if projector:
            args += ["--mmproj", projector["path"]]
        args += list(config.extra_args)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.logs_dir / f"managed-server-{time.time_ns()}.log"
        emit("status", {"message": f"Loading {model.name} with context {config.context}"})
        try:
            with self.log_path.open("wb") as log:
                self.process = OwnedProcess(args, str(executable.parent), log)
            client.client.log_path = str(self.log_path)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if stop.is_set():
                    raise Cancelled()
                if not self.running:
                    raise RuntimeError("llama-server exited during startup.")
                try:
                    client.client.request("/health", timeout=1)
                except RuntimeError:
                    stop.wait(.25)
                    continue
                models = client.client.list_models()
                if len(models) != 1:
                    raise RuntimeError("Managed server did not confirm one loaded model.")
                model_id = models[0]["name"]
                client.prepare(model_id)
                reported = client.model_info.get("model_path") or model_id
                if Path(reported).resolve() != model:
                    raise RuntimeError("Server reported a different model path.")
                actual_context = client.model_info.get("context_limit")
                if actual_context != config.context:
                    raise RuntimeError(f"Server context {actual_context!r} differs from requested {config.context}.")
                client.client.runtime_profile = config.runtime_name or executable.name
                client.client.required_capabilities = ("mtp",) if config.mtp else ()
                client.projector_identity = projector
                props = client.client.request("/props", timeout=3)
                modalities = props.get("modalities") or {}
                vision = modalities.get("vision") if isinstance(modalities, dict) else None
                client.vision_available = vision if type(vision) is bool else None
                client.model_info["runtime_profile"] = client.client.runtime_profile
                client.model_info["mtp_status"] = "awaiting verification" if config.mtp else "disabled"
                return client, model_id
            raise RuntimeError(f"llama-server did not become ready within {timeout} seconds.")
        except BaseException as exc:
            self.stop()
            if isinstance(exc, Cancelled):
                raise
            raise RuntimeError(f"{exc}\nServer log: {self.log_path}\n{self.tail()}") from exc
