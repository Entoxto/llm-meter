"""User-facing names kept separate from model identifiers and files."""
from pathlib import Path

from engine import normalize_host


def alias_key(backend, identifier, host=None):
    if backend == "gguf":
        return str(Path(identifier).resolve()).casefold()
    if backend == "ollama":
        if not host:
            raise ValueError("Для алиаса Ollama нужен адрес сервера.")
        return normalize_host(host)
    raise ValueError(f"Неизвестный источник моделей: {backend}")


def alias_for(settings, backend, identifier, host=None):
    aliases = settings.get("model_aliases", {})
    if backend == "gguf":
        return aliases.get("gguf", {}).get(alias_key(backend, identifier))
    return aliases.get("ollama", {}).get(alias_key(backend, identifier, host), {}).get(identifier)


def display_name(settings, backend, identifier, host=None):
    return alias_for(settings, backend, identifier, host) or (
        Path(identifier).name if backend == "gguf" else identifier)


def set_alias(settings, backend, identifier, value, host=None):
    """Set/clear an alias without changing the underlying identifier."""
    aliases = settings.setdefault("model_aliases", {})
    if backend == "gguf":
        rows = aliases.setdefault("gguf", {})
        key = alias_key(backend, identifier)
    else:
        rows = aliases.setdefault("ollama", {}).setdefault(alias_key(backend, identifier, host), {})
        key = identifier
    if value:
        rows[key] = value
    else:
        rows.pop(key, None)


def unique_labels(entries):
    """Map unique visible labels to exact identifiers without exposing paths."""
    result, used = {}, set()
    for identifier, title in entries:
        label = title
        suffix = 2
        while label.casefold() in used:
            label = f"{title} ({suffix})"
            suffix += 1
        result[label] = identifier
        used.add(label.casefold())
    return result
