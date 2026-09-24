"""Validated, immutable desired launch settings."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path


_CONTROLLED = {
    "-m", "--model", "-c", "--ctx-size", "--context-size", "-np", "--parallel",
    "-ngl", "--n-gpu-layers", "--gpu-layers", "--host", "--port",
    "-lv", "--log-verbosity", "--cache-type-k", "--cache-type-v",
    "--draft", "--draft-max", "--draft-model", "--spec-type", "--spec-draft-n-max",
    "-rea", "--reasoning", "--reasoning-budget", "-ctk", "-ctv",
    "--mmproj", "--mmproj-url", "--no-mmproj", "--mmproj-auto",
}

# llama-server may round a requested slot context slightly upward. Only the
# managed runtime may accept that adjustment; external endpoints stay exact.
MAX_MANAGED_CONTEXT_ROUNDUP = 255


@dataclass(frozen=True)
class LaunchConfig:
    model: str
    backend: str = "llama.cpp"
    context: int = 32768
    host: str = "http://127.0.0.1:8081"
    executable: str = ""
    managed: bool = True
    extra_args: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    runtime_name: str = ""
    model_id: str = ""
    gpu_layers: int = 99
    kv_type: str = "f16"
    reasoning: str = "auto"
    mtp: bool = False
    draft: int = 2
    mmproj: str = ""
    reasoning_budget: int | None = None

    def __post_init__(self):
        if not self.model or not self.model.strip():
            raise ValueError("Model is required.")
        if self.backend not in ("llama.cpp", "ollama"):
            raise ValueError(f"Unsupported backend: {self.backend}")
        if type(self.context) is not int or not 1 <= self.context <= 2_147_483_647:
            raise ValueError("Context must be an integer between 1 and 2147483647.")
        if type(self.gpu_layers) is not int or self.gpu_layers < 0:
            raise ValueError("GPU layers must be a nonnegative integer.")
        if self.kv_type not in ("f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"):
            raise ValueError("Unsupported KV cache type.")
        if self.reasoning not in ("auto", "on", "off"):
            raise ValueError("Reasoning must be auto, on, or off.")
        if self.reasoning_budget is not None:
            if type(self.reasoning_budget) is not int or not 1 <= self.reasoning_budget <= 2_147_483_647:
                raise ValueError("Reasoning budget must be a positive integer or None.")
            if self.reasoning == "off":
                raise ValueError("Reasoning budget cannot be used with reasoning off.")
            if self.backend != "llama.cpp" or not self.managed or "reasoning-budget" not in self.capabilities:
                raise ValueError("This runtime does not support a numeric reasoning budget.")
        if type(self.draft) is not int or self.draft < 1:
            raise ValueError("Draft tokens must be positive.")
        if not isinstance(self.mmproj, str):
            raise ValueError("Vision projector path must be text.")
        object.__setattr__(self, "extra_args", tuple(self.extra_args))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        for arg in self.extra_args:
            if not isinstance(arg, str) or not arg:
                raise ValueError("Each extra argument must be nonempty text.")
            key = arg.split("=", 1)[0]
            if key in _CONTROLLED or any(key.startswith(x + "=") for x in _CONTROLLED):
                raise ValueError(f"Extra argument conflicts with managed setting: {key}")
        if self.backend == "ollama":
            if self.managed or self.executable or self.extra_args or self.mtp or self.kv_type != "f16" or self.gpu_layers != 99 or self.mmproj:
                raise ValueError("Ollama uses an external service; llama-server launch options do not apply.")
        elif self.managed and not self.executable:
            raise ValueError("Managed llama.cpp requires an executable.")
        elif not self.managed and (self.extra_args or self.mtp or self.mmproj or self.kv_type != "f16"
                                   or self.gpu_layers != 99 or self.reasoning != "auto"):
            raise ValueError("External llama.cpp launch options cannot be applied by this app.")
        if self.mtp and "mtp" not in self.capabilities:
            raise ValueError("MTP requires an explicitly verified runtime capability.")
        if self.backend == "llama.cpp" and self.kv_type != "f16" and "kv-cache" not in self.capabilities:
            raise ValueError("KV quantization requires an explicitly verified runtime capability.")
        if self.backend == "llama.cpp" and self.reasoning != "auto" and "reasoning" not in self.capabilities:
            raise ValueError("Manual reasoning requires an explicitly verified runtime capability.")

    def to_dict(self) -> dict:
        value = asdict(self)
        value["extra_args"] = list(self.extra_args)
        value["capabilities"] = list(self.capabilities)
        return value

    @classmethod
    def from_dict(cls, value: dict) -> "LaunchConfig":
        return cls(**value)


def effective_context_matches(config: LaunchConfig, actual: object) -> bool:
    if type(actual) is not int:
        return False
    difference = actual - config.context
    return difference == 0 or (config.backend == "llama.cpp" and config.managed
                                and 0 < difference <= MAX_MANAGED_CONTEXT_ROUNDUP)


def projector_identity(path: str) -> dict:
    """Hash a selected projector and detect file replacement during the read."""
    selected = Path(path).resolve(strict=True)
    if not selected.is_file() or selected.suffix.lower() != ".gguf":
        raise ValueError("Select a readable GGUF vision projector file.")
    before = selected.stat()
    digest = hashlib.sha256()
    with selected.open("rb") as source:
        while chunk := source.read(4 * 1024 * 1024):
            digest.update(chunk)
    after = selected.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino):
        raise RuntimeError("Vision projector changed while its identity was verified.")
    return {"path": str(selected), "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns, "digest": digest.hexdigest(),
            "identity_verified": True}
