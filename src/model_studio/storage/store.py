"""Small SQLite repository. Each operation owns and closes its connection."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _object(value: str | None) -> dict:
    return json.loads(value) if value else {}


def _page(limit: int | None, offset: int) -> tuple[str, tuple[int, ...]]:
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer or None")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if limit is None and offset == 0:
        return "", ()
    return " LIMIT ? OFFSET ?", (limit if limit is not None else -1, offset)


class Store:
    """Persist Qt-friendly dictionaries with immutable benchmark snapshots.

    WAL is enabled only with SQLite 3.51.3+, the fixed WAL-reset release. An
    arbitrary Python may bundle an affected SQLite, so older versions use
    DELETE journaling. Connections are short-lived and never shared by threads.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_mode = "WAL" if sqlite3.sqlite_version_info >= (3, 51, 3) else "DELETE"
        self._migrate()

    @contextmanager
    def _connection(self, write: bool = False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=5000")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _migrate(self) -> None:
        with self._connection(write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
            if not db.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone():
                schema = """
                    CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                    CREATE TABLE projects (id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL, last_used TEXT NOT NULL);
                    CREATE TABLE models (id TEXT PRIMARY KEY, backend TEXT NOT NULL,
                        locator TEXT NOT NULL, fingerprint TEXT, digest TEXT,
                        available INTEGER NOT NULL, identity_verified INTEGER NOT NULL,
                        payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                    CREATE INDEX models_locator ON models(backend,locator);
                    CREATE INDEX models_digest ON models(backend,digest);
                    CREATE TABLE benchmark_results (id TEXT PRIMARY KEY, model_id TEXT,
                        research_id TEXT, status TEXT, created_at TEXT NOT NULL,
                        payload TEXT NOT NULL, FOREIGN KEY(model_id) REFERENCES models(id));
                    CREATE INDEX result_model ON benchmark_results(model_id,created_at);
                    CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                    CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL, payload TEXT NOT NULL,
                        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
                    CREATE INDEX message_order ON messages(conversation_id,created_at);
                    CREATE TABLE research_jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL);
                    CREATE TABLE imports (source TEXT PRIMARY KEY, digest TEXT NOT NULL,
                        status TEXT NOT NULL, imported_at TEXT NOT NULL);
                """
                for statement in schema.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            # Future schema versions are applied here, one numbered transaction at a time.
        with self._connection(write=True) as db:
            if not db.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
                db.execute("""CREATE TABLE installations (
                    id TEXT PRIMARY KEY, model_id TEXT NOT NULL, backend TEXT NOT NULL,
                    locator TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    available INTEGER NOT NULL, payload TEXT NOT NULL,
                    FOREIGN KEY(model_id) REFERENCES models(id))""")
                db.execute("CREATE UNIQUE INDEX installation_identity ON installations(model_id,backend,locator,fingerprint)")
                db.execute("CREATE INDEX installation_locator ON installations(backend,locator)")
                for old in db.execute("SELECT id,backend,locator,fingerprint,available,payload FROM models"):
                    db.execute("INSERT INTO installations VALUES (?,?,?,?,?,?,?)",
                               (str(uuid4()), old["id"], old["backend"], old["locator"],
                                old["fingerprint"] or "null", old["available"], old["payload"]))
                db.execute("INSERT INTO schema_migrations(version) VALUES (2)")
        with self._connection() as db:
            db.execute(f"PRAGMA journal_mode={self.journal_mode}")

    def settings(self) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT payload FROM settings WHERE id=1").fetchone()
            return _object(row[0]) if row else {}

    def save_settings(self, settings: dict) -> dict:
        payload = dict(settings)
        with self._connection(write=True) as db:
            db.execute("INSERT INTO settings(id,payload) VALUES (1,?) "
                       "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (_json(payload),))
        return payload

    def add_project(self, path: str | Path) -> dict:
        resolved = str(Path(path).expanduser().resolve(strict=True))
        if not Path(resolved).is_dir():
            raise ValueError("Project path must be a directory")
        row = {"id": str(uuid4()), "path": resolved,
               "name": Path(resolved).name, "last_used": _now()}
        with self._connection(write=True) as db:
            old = db.execute("SELECT id FROM projects WHERE path=?", (resolved,)).fetchone()
            if old:
                row["id"] = old[0]
                db.execute("UPDATE projects SET last_used=? WHERE id=?", (row["last_used"], row["id"]))
            else:
                db.execute("INSERT INTO projects(id,path,name,last_used) VALUES (:id,:path,:name,:last_used)", row)
        return row

    def projects(self) -> list[dict]:
        with self._connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY last_used DESC")]

    def upsert_model(self, record: dict) -> dict:
        row = dict(record)
        row.setdefault("id", str(uuid4()))
        row.setdefault("available", True)
        row.setdefault("identity_verified", False)
        row.setdefault("digest", None)
        row.setdefault("path", "")
        row.setdefault("backend", "gguf")
        locator = row.get("locator") or row.get("path") or f"{row.get('host', '')}/{row.get('name', '')}"
        row["locator"] = str(locator)
        stamp = _json(row.get("fingerprint")) if row.get("fingerprint") is not None else None
        now = _now()
        with self._connection(write=True) as db:
            existing = db.execute("SELECT created_at FROM models WHERE id=?", (row["id"],)).fetchone()
            row["created_at"] = existing[0] if existing else row.get("created_at", now)
            row["updated_at"] = now
            db.execute("""INSERT INTO models
                (id,backend,locator,fingerprint,digest,available,identity_verified,payload,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                backend=excluded.backend,locator=excluded.locator,fingerprint=excluded.fingerprint,
                digest=excluded.digest,available=excluded.available,
                identity_verified=excluded.identity_verified,payload=excluded.payload,
                updated_at=excluded.updated_at""",
                (row["id"], row["backend"], row["locator"], stamp, row["digest"],
                 int(row["available"]), int(row["identity_verified"]), _json(row),
                 row["created_at"], row["updated_at"]))
            db.execute("""INSERT INTO installations
                (id,model_id,backend,locator,fingerprint,available,payload) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(model_id,backend,locator,fingerprint) DO UPDATE SET
                available=excluded.available,payload=excluded.payload""",
                (str(uuid4()), row["id"], row["backend"], row["locator"],
                 stamp or "null", int(row["available"]), _json(row)))
        return row

    def installations(self) -> list[dict]:
        with self._connection() as db:
            return [{"id": r["id"], "model_id": r["model_id"], "backend": r["backend"],
                     "locator": r["locator"], "fingerprint": json.loads(r["fingerprint"]),
                     "available": bool(r["available"])} for r in db.execute(
                         "SELECT id,model_id,backend,locator,fingerprint,available FROM installations")]

    def reconcile_verified_model(self, provisional_id: str) -> dict:
        """Relink a moved GGUF to a missing proven artifact, retaining its UUID."""
        with self._connection(write=True) as db:
            provisional = db.execute("SELECT * FROM models WHERE id=?", (provisional_id,)).fetchone()
            if provisional is None:
                raise KeyError(provisional_id)
            row = _object(provisional["payload"])
            if row.get("backend") != "gguf" or not row.get("identity_verified") or not row.get("digest"):
                return row
            old = db.execute("""SELECT * FROM models WHERE backend='gguf' AND digest=?
                AND identity_verified=1 AND available=0 AND id<>? ORDER BY created_at LIMIT 1""",
                (row["digest"], provisional_id)).fetchone()
            if old is None or db.execute("SELECT 1 FROM benchmark_results WHERE model_id=? LIMIT 1",
                                         (provisional_id,)).fetchone():
                return row
            canonical = _object(old["payload"])
            row["id"] = old["id"]
            row["created_at"] = old["created_at"]
            if canonical.get("alias") and not row.get("alias"):
                row["alias"] = canonical["alias"]
                row["name"] = canonical["alias"]
            row["updated_at"] = _now()
            # One proven artifact, two installation records (old absent, new present).
            db.execute("UPDATE installations SET model_id=? WHERE model_id=?",
                       (old["id"], provisional_id))
            db.execute("DELETE FROM models WHERE id=?", (provisional_id,))
            db.execute("""UPDATE models SET locator=?,fingerprint=?,digest=?,available=1,
                identity_verified=1,payload=?,updated_at=? WHERE id=?""",
                (row["locator"], provisional["fingerprint"], row["digest"],
                 _json(row), row["updated_at"], old["id"]))
        return row

    def models(self, include_missing: bool = True) -> list[dict]:
        sql = "SELECT payload FROM models" + ("" if include_missing else " WHERE available=1") + " ORDER BY updated_at DESC"
        with self._connection() as db:
            return [_object(r[0]) for r in db.execute(sql)]

    def mark_missing(self, model_id: str) -> None:
        with self._connection(write=True) as db:
            old = db.execute("SELECT payload FROM models WHERE id=?", (model_id,)).fetchone()
            if old is None:
                raise KeyError(model_id)
            row = _object(old[0]); row["available"] = False; row["updated_at"] = _now()
            stamp = _json(row.get("fingerprint")) if row.get("fingerprint") is not None else "null"
            db.execute("""UPDATE installations SET available=0 WHERE model_id=? AND
                backend=? AND locator=? AND fingerprint=?""",
                (model_id, row["backend"], row["locator"], stamp))
            alternate = db.execute("SELECT payload FROM installations WHERE model_id=? AND available=1 LIMIT 1",
                                   (model_id,)).fetchone()
            if alternate:
                next_row = _object(alternate[0])
                next_row.update(id=model_id, digest=row.get("digest"),
                                identity_verified=row.get("identity_verified"),
                                alias=row.get("alias"), available=True,
                                created_at=row.get("created_at"), updated_at=row["updated_at"])
                if next_row.get("alias"):
                    next_row["name"] = next_row["alias"]
                stamp = _json(next_row.get("fingerprint")) if next_row.get("fingerprint") is not None else None
                db.execute("""UPDATE models SET locator=?,fingerprint=?,available=1,
                    payload=?,updated_at=? WHERE id=?""",
                    (next_row["locator"], stamp, _json(next_row), next_row["updated_at"], model_id))
                return
            db.execute("UPDATE models SET available=0,payload=?,updated_at=? WHERE id=?",
                       (_json(row), row["updated_at"], model_id))

    def mark_installation_missing(self, installation_id: str) -> None:
        """Mark one observed location absent without hiding another installation."""
        with self._connection(write=True) as db:
            installation = db.execute("SELECT * FROM installations WHERE id=?", (installation_id,)).fetchone()
            if installation is None:
                raise KeyError(installation_id)
            if not installation["available"]:
                return
            db.execute("UPDATE installations SET available=0 WHERE id=?", (installation_id,))
            current = db.execute("SELECT payload FROM models WHERE id=?", (installation["model_id"],)).fetchone()
            if current is None:
                return
            row = _object(current[0])
            current_stamp = _json(row.get("fingerprint")) if row.get("fingerprint") is not None else "null"
            if row.get("locator") != installation["locator"] or current_stamp != installation["fingerprint"]:
                return
            alternate = db.execute("SELECT payload FROM installations WHERE model_id=? AND available=1 LIMIT 1",
                                   (installation["model_id"],)).fetchone()
            now = _now()
            if alternate:
                replacement = _object(alternate[0])
                replacement.update(id=row["id"], digest=row.get("digest"),
                                   identity_verified=row.get("identity_verified"),
                                   alias=row.get("alias"), available=True,
                                   created_at=row.get("created_at"), updated_at=now)
                if replacement.get("alias"):
                    replacement["name"] = replacement["alias"]
                stamp = _json(replacement.get("fingerprint")) if replacement.get("fingerprint") is not None else None
                db.execute("""UPDATE models SET locator=?,fingerprint=?,available=1,payload=?,updated_at=?
                    WHERE id=?""", (replacement["locator"], stamp, _json(replacement), now, row["id"]))
            else:
                row.update(available=False, updated_at=now)
                db.execute("UPDATE models SET available=0,payload=?,updated_at=? WHERE id=?",
                           (_json(row), now, row["id"]))

    def save_result(self, result: dict) -> dict:
        row = dict(result)
        row.setdefault("id", str(uuid4()))
        row.setdefault("created_at", _now())
        # Every field, including unknown legacy fields, remains in the immutable snapshot.
        with self._connection(write=True) as db:
            db.execute("""INSERT INTO benchmark_results(id,model_id,research_id,status,created_at,payload)
                VALUES (?,?,?,?,?,?)""", (row["id"], row.get("model_id"), row.get("research_id"),
                                       row.get("status"), row["created_at"], _json(row)))
        return row

    def results(self, model_id: str | None = None, limit: int | None = None,
                offset: int = 0) -> list[dict]:
        suffix, page_args = _page(limit, offset)
        with self._connection() as db:
            if model_id is None:
                rows = db.execute("SELECT payload FROM benchmark_results ORDER BY created_at DESC,id DESC" + suffix,
                                  page_args)
            else:
                rows = db.execute("SELECT payload FROM benchmark_results WHERE model_id=? "
                                  "ORDER BY created_at DESC,id DESC" + suffix, (model_id, *page_args))
            return [_object(r[0]) for r in rows]

    def create_conversation(self, title: str = "Новый диалог") -> dict:
        row = {"id": str(uuid4()), "title": title, "created_at": _now(), "updated_at": _now()}
        with self._connection(write=True) as db:
            db.execute("INSERT INTO conversations VALUES (:id,:title,:created_at,:updated_at)", row)
        return row

    def conversations(self, limit: int | None = None, offset: int = 0) -> list[dict]:
        suffix, args = _page(limit, offset)
        with self._connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM conversations ORDER BY updated_at DESC,id DESC" + suffix,
                                                args)]

    def messages(self, conversation_id: str, limit: int | None = None,
                 offset: int = 0) -> list[dict]:
        suffix, page_args = _page(limit, offset)
        with self._connection() as db:
            return [_object(r[0]) for r in db.execute(
                "SELECT payload FROM messages WHERE conversation_id=? ORDER BY created_at,id" + suffix,
                (conversation_id, *page_args))]

    def save_message(self, conversation_id: str, role: str, text: str, status: str = "complete",
                     reasoning: str = "", metadata: dict | None = None,
                     message_id: str | None = None) -> dict:
        now = _now()
        row = {"id": message_id or str(uuid4()), "conversation_id": conversation_id,
               "role": role, "text": text, "status": status, "reasoning": reasoning,
               "metadata": metadata or {}, "created_at": now, "updated_at": now}
        with self._connection(write=True) as db:
            old = db.execute("SELECT created_at FROM messages WHERE id=? AND conversation_id=?",
                             (row["id"], conversation_id)).fetchone()
            if old:
                row["created_at"] = old[0]
            db.execute("""INSERT INTO messages(id,conversation_id,role,status,created_at,updated_at,payload)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,updated_at=excluded.updated_at,payload=excluded.payload""",
                (row["id"], conversation_id, role, status, row["created_at"], now, _json(row)))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        return row

    def delete_conversation(self, conversation_id: str) -> None:
        with self._connection(write=True) as db:
            db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    def create_research(self, plan: dict) -> dict:
        row = {"id": str(uuid4()), "plan": dict(plan), "status": "planned",
               "created_at": _now(), "updated_at": _now(), "completed_steps": []}
        with self._connection(write=True) as db:
            db.execute("INSERT INTO research_jobs VALUES (?,?,?,?,?)",
                       (row["id"], row["status"], row["created_at"], row["updated_at"], _json(row)))
        return row

    def update_research(self, research_id: str, fields: dict) -> dict:
        with self._connection(write=True) as db:
            old = db.execute("SELECT payload FROM research_jobs WHERE id=?", (research_id,)).fetchone()
            if old is None:
                raise KeyError(research_id)
            row = _object(old[0]); row.update(fields); row["id"] = research_id; row["updated_at"] = _now()
            db.execute("UPDATE research_jobs SET status=?,updated_at=?,payload=? WHERE id=?",
                       (row["status"], row["updated_at"], _json(row), research_id))
        return row

    def research_jobs(self) -> list[dict]:
        with self._connection() as db:
            return [_object(r[0]) for r in db.execute("SELECT payload FROM research_jobs ORDER BY created_at DESC")]

    def recover_interrupted(self) -> dict:
        """Mark incomplete work after a restart; preserve text and measurements."""
        summary = {"messages": 0, "research_jobs": 0}
        now = _now()
        with self._connection(write=True) as db:
            for old in db.execute("SELECT id,payload FROM messages WHERE status='streaming'").fetchall():
                row = _object(old["payload"])
                row.update(status="interrupted", updated_at=now)
                db.execute("UPDATE messages SET status='interrupted',updated_at=?,payload=? WHERE id=?",
                           (now, _json(row), old["id"]))
                summary["messages"] += 1
            for old in db.execute("SELECT id,payload FROM research_jobs WHERE status IN ('running','planned')").fetchall():
                row = _object(old["payload"])
                row.update(status="interrupted", stop_reason="application_restart", updated_at=now)
                db.execute("UPDATE research_jobs SET status='interrupted',updated_at=?,payload=? WHERE id=?",
                           (now, _json(row), old["id"]))
                summary["research_jobs"] += 1
        return summary

    def backup(self, path: str | Path) -> Path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination == self.path:
            raise ValueError("Backup destination is the live database")
        with self._connection() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
        return destination

    def restore_backup(self, path: str | Path) -> Path:
        """Validate a backup, preserve the live DB, then replace it offline.

        The caller must stop all operations first. This store holds no permanent
        connection, but cannot coordinate another process using the same file.
        Returns the path of the preserved pre-restore database.
        """
        source = Path(path).expanduser().resolve(strict=True)
        if source == self.path:
            raise ValueError("Backup source is the live database")
        required = {"schema_migrations", "settings", "models", "benchmark_results",
                    "conversations", "messages", "projects", "research_jobs", "imports"}
        with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as candidate:
            if candidate.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Backup failed SQLite integrity check")
            tables = {r[0] for r in candidate.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required <= tables or not candidate.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=1").fetchone():
                raise ValueError("Backup has an unsupported schema")
            stage = self.path.with_name(f".{self.path.name}.{uuid4().hex}.restore")
            try:
                with closing(sqlite3.connect(stage)) as target:
                    candidate.backup(target)
                backup_dir = self.path.parent / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                preserved = self.backup(backup_dir / f"before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.db")
                os.replace(stage, self.path)
            finally:
                stage.unlink(missing_ok=True)
        self._migrate()
        return preserved

    def import_legacy(self, root: str | Path) -> dict:
        """Import valid legacy JSON once per source digest; never infer identity."""
        root = Path(root).expanduser().resolve()
        summary = {"settings": 0, "results": 0, "skipped": 0, "errors": []}
        candidates = [root / "settings.json", root / "runtime_profiles.local.json"]
        candidates.extend(sorted((root / "results").glob("*.json")) if (root / "results").is_dir() else [])
        pending = []
        for file in candidates:
            if not file.is_file():
                continue
            try:
                raw = file.read_bytes()
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("JSON root is not an object")
                source = str(file)
                digest = hashlib.sha256(raw).hexdigest()
                with self._connection() as db:
                    prior = db.execute("SELECT digest FROM imports WHERE source=?", (source,)).fetchone()
                if prior and prior[0] == digest:
                    summary["skipped"] += 1
                    continue
                pending.append((file, source, digest, data))
            except (OSError, ValueError) as exc:
                summary["errors"].append(f"{file}: {exc}")
        if not pending:
            return summary
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        self.backup(backup_dir / f"before-import-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.db")
        for file, source, digest, data in pending:
            try:
                with self._connection(write=True) as db:
                    if file.name in ("settings.json", "runtime_profiles.local.json"):
                        old = db.execute("SELECT payload FROM settings WHERE id=1").fetchone()
                        merged = _object(old[0]) if old else {}
                        if file.name == "runtime_profiles.local.json":
                            for key in ("runtime_profiles", "model_profiles"):
                                if isinstance(data.get(key), dict):
                                    merged.setdefault(key, {}).update(data[key])
                        else:
                            merged.update(data)
                        db.execute("INSERT INTO settings VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                                   (_json(merged),))
                        summary["settings"] += 1
                    else:
                        result = dict(data)
                        result["id"] = str(uuid4())
                        result["legacy_source"] = source
                        result["identity_verified"] = False
                        result["comparison_limit"] = "Legacy artifact, environment and workload identity unverified"
                        result.setdefault("created_at", _now())
                        db.execute("INSERT INTO benchmark_results VALUES (?,?,?,?,?,?)",
                                   (result["id"], None, None, result.get("status", "historical"),
                                    result["created_at"], _json(result)))
                        summary["results"] += 1
                    db.execute("INSERT INTO imports VALUES (?,?,?,?) ON CONFLICT(source) DO UPDATE SET digest=excluded.digest,status=excluded.status,imported_at=excluded.imported_at",
                               (source, digest, "imported", _now()))
            except (OSError, ValueError, sqlite3.Error) as exc:
                summary["errors"].append(f"{file}: {exc}")
        return summary
