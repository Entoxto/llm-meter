"""Lifecycle of a local llama-server owned by this application only."""
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlsplit

from engine import Cancelled
from llama_cpp import LlamaCppClient


class ManagedServer:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.process = None
        self.config = None
        self.log_path = None

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    @property
    def model_path(self):
        return self.config[1] if self.config and self.running else None

    def stop(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.process = None
        self.config = None

    def tail(self):
        if not self.log_path:
            return ""
        try:
            with self.log_path.open("rb") as source:
                source.seek(0, 2)
                source.seek(max(0, source.tell() - 4000))
                return source.read().decode("utf-8", errors="replace")[-2000:]
        except OSError:
            return ""

    def ensure(self, executable, model, host, context, stop, emit, timeout=120, profile=None):
        profile = profile or {"name": "Обычный llama.cpp", "extra_args": [], "capabilities": []}
        executable = profile.get("executable", executable)
        extra_args = profile.get("extra_args", [])
        capabilities = profile.get("capabilities", [])
        executable, model = Path(executable).resolve(), Path(model).resolve()
        if not executable.is_file():
            raise ValueError(f"Runtime «{profile['name']}» не найден: {executable}. "
                             "Проверьте путь или установите совместимую сборку.")
        if not model.is_file() or model.suffix.lower() != ".gguf":
            raise ValueError("Выберите существующую GGUF-модель.")
        client = LlamaCppClient(host, context=context)
        url = urlsplit(client.host)
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost") or url.path:
            raise ValueError("Для управляемого сервера нужен адрес http://127.0.0.1:порт (без /v1).")
        config = (str(executable), str(model), client.host, context, tuple(extra_args), tuple(capabilities))
        if stop.is_set():
            raise Cancelled()
        if self.running and self.config != config:
            emit("status", "Перезапуск llama-server с новым контекстом…")
            self.stop()
        if not self.running:
            # Refuse to adopt/kill a process which was not started by us.
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", url.port))
                except OSError as exc:
                    raise RuntimeError(f"Порт {url.port} занят другим сервером. Выберите свободный порт "
                                       "или режим внешнего сервера.") from exc
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = self.output_dir / f"managed-server-{time.time_ns()}.log"
            args = [str(executable), "-m", str(model), "-c", str(context), "-np", "1",
                    "-ngl", "99", "--host", "127.0.0.1", "--port", str(url.port), "-lv", "4",
                    *extra_args]
            emit("status", f"Загрузка GGUF · контекст {context:,} · ожидание до {timeout} с…")
            with self.log_path.open("wb") as log:
                self.process = subprocess.Popen(args, cwd=executable.parent, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.config = config
        client.log_path = str(self.log_path)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if stop.is_set():
                    raise Cancelled()
                if not self.running:
                    raise RuntimeError("llama-server завершился при загрузке. Возможно, недостаточно памяти "
                                       "или сборка не поддерживает модель.")
                try:
                    client.request("/health", timeout=1)
                except RuntimeError:
                    stop.wait(.25)
                    continue
                models = client.list_models()
                if len(models) != 1:
                    raise RuntimeError("Управляемый сервер не подтвердил единственную загруженную модель.")
                model_id = models[0]["name"]
                client.prepare(model_id)
                actual_path = client.model_info.get("model_path") or model_id
                if Path(actual_path).resolve() != model:
                    raise RuntimeError("Сервер сообщил другую модель; запуск отменён.")
                actual = client.model_info.get("context_limit")
                if actual != context:
                    raise RuntimeError(f"Сервер выделил context limit {actual}, выбран {context}. "
                                       "Проверьте поддерживаемый размер контекста.")
                client.runtime_profile = profile.get("name") or "Обычный llama.cpp"
                client.required_capabilities = tuple(capabilities)
                client.model_info["runtime_profile"] = client.runtime_profile
                client.model_info["mtp_status"] = ("ожидает проверки" if "mtp" in capabilities else "выключен")
                return client, model_id
            raise RuntimeError(f"llama-server не запустился за {timeout} с.")
        except BaseException as exc:
            self.stop()
            if isinstance(exc, Cancelled):
                raise
            raise RuntimeError(f"{exc}\nВыберите меньший контекст и повторите тест. "
                               f"Лог: {self.log_path}\n{self.tail()}") from exc
