"""Declarative, per-GGUF managed llama-server profiles.

Only paths explicitly associated in settings.json use a custom runtime. All
other GGUFs retain the existing default executable and launch arguments.
"""
from pathlib import Path


def model_key(path):
    return str(Path(path).resolve()).casefold()


def has_managed_runtime(settings):
    executables = [settings.get("server_exe", "")]
    executables += [profile.get("executable", "") for profile in
                    settings.get("runtime_profiles", {}).values() if isinstance(profile, dict)]
    return (any(path and Path(path).is_file() for path in executables)
            and any(Path(folder).is_dir() for folder in settings.get("model_dirs", [])))


def runtime_for(settings, model, default_executable):
    associations = settings.get("model_profiles", {})
    profile_id = next((value for path, value in associations.items()
                       if model_key(path) == model_key(model)), None)
    if not profile_id:
        return {"name": "Обычный llama.cpp", "executable": default_executable,
                "extra_args": [], "capabilities": []}
    profile = settings.get("runtime_profiles", {}).get(profile_id)
    if not isinstance(profile, dict):
        raise ValueError(f"Профиль runtime «{profile_id}» не найден в настройках.")
    executable = profile.get("executable")
    args = profile.get("extra_args", [])
    capabilities = profile.get("capabilities", [])
    if not isinstance(executable, str) or not executable:
        raise ValueError(f"У профиля «{profile_id}» не указан llama-server.exe.")
    if (not isinstance(args, list) or not all(isinstance(a, str) for a in args)
            or not isinstance(capabilities, list)
            or not all(isinstance(c, str) for c in capabilities)):
        raise ValueError(f"Некорректные аргументы или возможности профиля «{profile_id}».")
    return {"id": profile_id, "name": profile.get("name") or profile_id,
            "executable": executable, "extra_args": args,
            "capabilities": capabilities}
