import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_studio.storage import Store
from model_studio.platform.paths import ensure_data_dirs


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "studio.db")

    def test_settings_chat_results_and_reopen(self):
        self.store.save_settings({"model_aliases": {"gguf": {"x": "Пример"}}})
        model = self.store.upsert_model({"backend": "gguf", "path": "X.gguf", "name": "X"})
        result = self.store.save_result({"model_id": model["id"], "status": "complete",
                                         "config": {"context": 2048}, "runs": [{"tokens_per_second": None}]})
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_result(result)
        chat = self.store.create_conversation()
        msg = self.store.save_message(chat["id"], "assistant", "часть", status="streaming")
        self.store.save_message(chat["id"], "assistant", "полный ответ", message_id=msg["id"],
                                metadata={"model_id": model["id"]})
        reopened = Store(self.store.path)
        self.assertEqual(reopened.settings()["model_aliases"]["gguf"]["x"], "Пример")
        self.assertEqual(reopened.results(model["id"])[0]["runs"][0]["tokens_per_second"], None)
        self.assertEqual(reopened.messages(chat["id"])[0]["text"], "полный ответ")
        reopened.mark_missing(model["id"])
        self.assertEqual(reopened.models(include_missing=False), [])
        self.assertEqual(len(reopened.results(model["id"])), 1)

    def test_backup_restore_checks_schema_and_keeps_previous_db(self):
        self.store.save_settings({"one": 1})
        backup = self.store.backup(self.root / "snapshot.db")
        self.store.save_settings({"one": 2})
        preserved = self.store.restore_backup(backup)
        self.assertEqual(self.store.settings(), {"one": 1})
        self.assertTrue(preserved.is_file())
        self.assertEqual(Store(preserved).settings(), {"one": 2})
        bad = self.root / "bad.db"
        sqlite3.connect(bad).close()
        with self.assertRaises(ValueError):
            self.store.restore_backup(bad)
        self.assertEqual(self.store.settings(), {"one": 1})

    def test_legacy_import_repeats_without_duplicates_and_keeps_unknown_identity(self):
        legacy = self.root / "legacy"
        (legacy / "results").mkdir(parents=True)
        (legacy / "settings.json").write_text(json.dumps({"runtime_profiles": {"a": {"name": "old"}}}))
        (legacy / "runtime_profiles.local.json").write_text(json.dumps(
            {"runtime_profiles": {"a": {"name": "local"}}, "model_profiles": {"x": "a"}}))
        (legacy / "results" / "one.json").write_text(json.dumps(
            {"model": "same-name", "runs": [{"tokens_per_second": 12.3}]}))
        (legacy / "results" / "broken.json").write_text("{")
        first = self.store.import_legacy(legacy)
        second = self.store.import_legacy(legacy)
        self.assertEqual(first["results"], 1)
        self.assertEqual(len(first["errors"]), 1)
        self.assertEqual(second["results"], 0)
        self.assertEqual(self.store.settings()["runtime_profiles"]["a"]["name"], "local")
        self.assertFalse(self.store.results()[0]["identity_verified"])
        self.assertIsNone(self.store.results()[0].get("model_id"))
        self.assertEqual(len(self.store.results()), 1)

    def test_paths_override_creates_expected_directories(self):
        tree = ensure_data_dirs(self.root / "данные")
        self.assertEqual(tree["database"].name, "studio.db")
        for folder in ("reports", "logs", "cache", "backups"):
            self.assertTrue(tree[folder].is_dir())

    def test_restart_recovery_preserves_partial_work_and_is_idempotent(self):
        chat = self.store.create_conversation()
        partial = self.store.save_message(chat["id"], "assistant", "частичный ответ",
                                          status="streaming", reasoning="отдельное поле")
        complete = self.store.save_message(chat["id"], "user", "вопрос")
        job = self.store.create_research({"contexts": [2048]})
        self.store.update_research(job["id"], {"status": "running",
                                               "completed_steps": [{"result_id": "earlier"}]})
        saved = self.store.save_result({"status": "completed", "runs": [{"tokens": 100}]})
        self.assertEqual(self.store.recover_interrupted(), {"messages": 1, "research_jobs": 1})
        self.assertEqual(self.store.recover_interrupted(), {"messages": 0, "research_jobs": 0})
        messages = self.store.messages(chat["id"])
        self.assertEqual(next(m for m in messages if m["id"] == partial["id"])["text"], "частичный ответ")
        self.assertEqual(next(m for m in messages if m["id"] == partial["id"])["reasoning"], "отдельное поле")
        self.assertEqual(next(m for m in messages if m["id"] == partial["id"])["status"], "interrupted")
        self.assertEqual(next(m for m in messages if m["id"] == complete["id"])["status"], "complete")
        recovered = self.store.research_jobs()[0]
        self.assertEqual(recovered["stop_reason"], "application_restart")
        self.assertEqual(recovered["completed_steps"], [{"result_id": "earlier"}])
        self.assertEqual(self.store.results()[0]["id"], saved["id"])

    def test_optional_history_pages_do_not_change_default_queries(self):
        chat = self.store.create_conversation()
        self.store.save_message(chat["id"], "user", "one")
        self.store.save_message(chat["id"], "user", "two")
        self.assertEqual(len(self.store.messages(chat["id"])), 2)
        self.assertEqual(len(self.store.messages(chat["id"], limit=1, offset=1)), 1)
        self.assertEqual(len(self.store.conversations(limit=1)), 1)
        self.store.save_result({"status": "complete"})
        self.store.save_result({"status": "complete"})
        self.assertEqual(len(self.store.results()), 2)
        self.assertEqual(len(self.store.results(limit=1, offset=1)), 1)
        with self.assertRaises(ValueError):
            self.store.results(limit=0)
