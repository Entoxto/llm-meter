"""Local model discovery and identity, without UI or full-weight scan reads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from inventory import (delete_gguf, delete_ollama, fingerprint, linked,
                       local_model_processes, scan_gguf, testable_gguf)
from model_aliases import alias_for, display_name

from .storage import Store
from .backends.ollama import is_context_profile


class Catalog:
    def __init__(self, store: Store, ollama_client_factory: Callable | None = None,
                 llama_client_factory: Callable | None = None):
        self.store = store
        self.ollama_client_factory = ollama_client_factory
        self.llama_client_factory = llama_client_factory
        self.errors: list[str] = []
        self.connections: dict[str, bool] = {}

    def scan(self, settings: dict) -> list[dict]:
        """Reconcile observed installations, retaining disappeared history."""
        self.errors = []
        self.connections = {}
        known = self.store.models()
        known_installations = self.store.installations()
        seen_installations: set[tuple[str, str, str]] = set()
        observed_gguf_locators: set[str] = set()
        inspected_ollama_hosts: set[str] = set()
        inspected_llama_hosts: set[str] = set()
        gguf, errors = scan_gguf(settings.get("model_dirs", []), settings.get("known_files", []))
        self.errors.extend(errors)
        for item in gguf:
            locator = str(Path(item["path"]).resolve())
            observed_gguf_locators.add(locator)
            stamp = list(item["fingerprint"])
            seen_installations.add(("gguf", locator, json.dumps(stamp)))
            row = self._existing(known, known_installations, "gguf", locator, stamp)
            record = dict(row or {})
            alias = alias_for(settings, "gguf", locator)
            record.update({"backend": "gguf", "locator": locator, "path": locator,
                           "name": alias or record.get("alias") or item["name"],
                           "filename": item["name"], "size_bytes": item["size"],
                           "fingerprint": stamp, "quantization": item.get("quant"),
                           "architecture": item.get("architecture"),
                           "testable": testable_gguf({"name": item["name"],
                                                      "architecture": item.get("architecture")}),
                           "available": True})
            if alias:
                record["alias"] = alias
            if not row:
                record.update(identity_verified=False, digest=None)
            if item.get("error"):
                record["metadata_error"] = item["error"]
            self.store.upsert_model(record)

        host = (settings.get("backend_hosts") or {}).get("Ollama") or settings.get("ollama_host")
        if host:
            try:
                client = self._ollama(host)
                items = client.list_models()
                self.connections["ollama"] = True
                inspected_ollama_hosts.add(client.host)
                for item in items:
                    name = item.get("name")
                    if not name or is_context_profile(name):
                        continue
                    locator = f"{client.host}/{name}"
                    digest = item.get("digest")
                    seen_installations.add(("ollama", locator, json.dumps(digest)))
                    # A changed tag digest is a new artifact; old results retain the old ID.
                    row = self._existing(known, known_installations, "ollama", locator, digest)
                    record = dict(row or {})
                    record.update({"backend": "ollama", "locator": locator, "host": client.host,
                                   "path": "", "tag": name,
                                   "name": display_name(settings, "ollama", name, client.host),
                                   "size_bytes": item.get("size"), "digest": digest,
                                   "fingerprint": digest, "identity_verified": bool(digest),
                                   "available": True})
                    self.store.upsert_model(record)
            except Exception as exc:
                self.connections["ollama"] = False
                # Discovery failure is reported, never interpreted as removal.
                self.errors.append(f"Ollama {host}: {exc}")

        if settings.get("enable_external_llama"):
            llama_host = settings.get("external_host") or (settings.get("backend_hosts") or {}).get("llama.cpp")
            if llama_host:
                try:
                    client = self._llama(llama_host)
                    items = client.list_models()
                    self.connections["external_llama"] = True
                    inspected_llama_hosts.add(client.host)
                    for item in items:
                        name = item.get("name") or item.get("id")
                        if not name:
                            continue
                        locator = f"{client.host}/{name}"
                        digest = item.get("digest")
                        seen_installations.add(("llama.cpp", locator, json.dumps(digest)))
                        row = self._existing(known, known_installations, "llama.cpp", locator, digest)
                        meta = item.get("meta") or {}
                        record = dict(row or {})
                        record.update({"backend": "llama.cpp", "locator": locator,
                                       "host": client.host, "tag": name, "path": "", "name": record.get("alias") or name,
                                       "size_bytes": meta.get("size") or item.get("size"),
                                       "context_limit": meta.get("n_ctx"),
                                       "digest": digest, "fingerprint": digest,
                                       "identity_verified": bool(digest), "available": True})
                        self.store.upsert_model(record)
                except Exception as exc:
                    self.connections["external_llama"] = False
                    self.errors.append(f"External llama.cpp {llama_host}: {exc}")

        # Missing directories cannot prove disappearance of files in them.
        inaccessible = [str(Path(d).resolve()) for d in settings.get("model_dirs", [])
                        if not Path(d).is_dir()]
        for old in known_installations:
            if not old["available"]:
                continue
            key = (old["backend"], old["locator"], json.dumps(old["fingerprint"]))
            if key in seen_installations:
                continue
            if old["backend"] == "gguf":
                if any(Path(old["locator"]).is_relative_to(Path(d)) for d in inaccessible):
                    continue
                if old["locator"] not in observed_gguf_locators and Path(old["locator"]).is_file():
                    # A removed search directory is not evidence of file deletion.
                    continue
            elif old["backend"] == "ollama":
                if not any(old["locator"].startswith(host + "/") for host in inspected_ollama_hosts):
                    continue
            elif old["backend"] == "llama.cpp":
                if not any(old["locator"].startswith(host + "/") for host in inspected_llama_hosts):
                    continue
            self.store.mark_installation_missing(old["id"])
        return self.store.models()

    @staticmethod
    def _existing(rows: list[dict], installations: list[dict], backend: str,
                  locator: str, identity) -> dict | None:
        if identity is None:
            return None
        for row in rows:
            if row.get("backend") == backend and row.get("locator") == locator:
                prior = row.get("fingerprint") if backend == "gguf" else row.get("digest")
                if prior == identity:
                    return row
        for installation in installations:
            if installation["backend"] == backend and installation["locator"] == locator \
                    and installation["fingerprint"] == identity:
                return next((row for row in rows if row["id"] == installation["model_id"]), None)
        return None

    def _ollama(self, host: str):
        if self.ollama_client_factory:
            return self.ollama_client_factory(host)
        from engine import Client
        return Client(host)

    def _llama(self, host: str):
        if self.llama_client_factory:
            return self.llama_client_factory(host)
        from llama_cpp import LlamaCppClient
        return LlamaCppClient(host)

    def hash_model(self, model_id: str, cancel=None) -> dict:
        """Stream SHA-256 only on explicit/background verification; abort on change."""
        row = self._model(model_id)
        if row["backend"] != "gguf":
            raise ValueError("Only GGUF files are hashed locally")
        path = Path(row["path"])
        if linked(path) or not path.is_file():
            raise RuntimeError("Model file is unavailable")
        before = fingerprint(path)
        if list(before) != row.get("fingerprint"):
            raise RuntimeError("Model file changed since catalog scan")
        h = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(4 * 1024 * 1024):
                if cancel is not None and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
                    raise InterruptedError("Model hash cancelled")
                h.update(chunk)
        if fingerprint(path) != before:
            raise RuntimeError("Model file changed while hashing")
        row["digest"] = h.hexdigest()
        row["identity_verified"] = True
        saved = self.store.upsert_model(row)
        return self.store.reconcile_verified_model(saved["id"])

    def delete_model(self, model_id: str, settings: dict, client=None,
                     allow_unload: bool = False, active_guard=None) -> int:
        """Delete only the selected installation after fresh validation."""
        row = self._model(model_id)
        if not row.get("available"):
            raise RuntimeError("Model installation is already absent")
        if active_guard and active_guard(row):
            raise RuntimeError("Model is active; stop its session before deletion")
        if row["backend"] == "gguf":
            running = local_model_processes(row["path"])
            if running:
                raise RuntimeError(f"Model is used by process {running[0]}")
            legacy_row = {"path": row["path"], "fingerprint": row["fingerprint"]}
            freed = delete_gguf(legacy_row, settings.get("model_dirs", []),
                                settings.get("known_files", []))
        elif row["backend"] == "ollama":
            client = client or self._ollama(row["host"])
            delete_ollama(client, {"name": row["tag"], "digest": row["digest"],
                                   "size": row["size_bytes"]}, allow_unload)
            freed = int(row.get("size_bytes") or 0)
        elif row["backend"] == "llama.cpp":
            raise RuntimeError("External llama.cpp weights cannot be deleted by this application")
        else:
            raise ValueError(f"Unknown model backend: {row['backend']}")
        self.store.mark_missing(model_id)
        return freed

    def _model(self, model_id: str) -> dict:
        return next((row for row in self.store.models() if row["id"] == model_id), None) or self._missing(model_id)

    @staticmethod
    def _missing(model_id: str):
        raise KeyError(model_id)
