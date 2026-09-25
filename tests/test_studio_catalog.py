from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_studio.catalog import Catalog
from model_studio.storage import Store


def gguf(path):
    def string(value):
        raw = value.encode()
        return struct.pack("<Q", len(raw)) + raw
    data = b"GGUF" + struct.pack("<IQQ", 3, 0, 3)
    for key, value in [("general.architecture", "test"), ("general.name", "test model")]:
        data += string(key) + struct.pack("<I", 8) + string(value)
    data += string("general.file_type") + struct.pack("<II", 4, 15)
    path.write_bytes(data)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / "модель.gguf"
        gguf(self.model)
        self.store = Store(self.root / "studio.db")
        self.catalog = Catalog(self.store)
        self.settings = {"model_dirs": [str(self.root)], "known_files": []}

    def test_user_alias_survives_scan_and_can_be_reset_without_changing_results(self):
        self.store.save_settings(self.settings)
        first = self.catalog.scan(self.settings)[0]
        verified = self.catalog.hash_model(first["id"])
        result = self.store.save_result({"model_id": first["id"], "status": "completed"})
        renamed = self.store.rename_model(first["id"], "  Моя модель  ")
        self.assertEqual(renamed["name"], "Моя модель")
        scanned = self.catalog.scan(self.store.settings())[0]
        for key in ("id", "locator", "path", "digest", "identity_verified"):
            self.assertEqual(scanned[key], verified[key])
        self.assertEqual(scanned["name"], "Моя модель")
        self.assertEqual(self.store.results(first["id"])[0]["id"], result["id"])
        self.store.rename_model(first["id"], "")
        self.assertEqual(self.catalog.scan(self.store.settings())[0]["name"], self.model.name)

    def test_ollama_alias_keeps_tag_and_host_scoping(self):
        from unittest.mock import Mock
        host = "http://127.0.0.1:11434"
        client = Mock(host=host)
        client.list_models.return_value = [{"name": "tag-32k:latest", "digest": "abc"}]
        self.catalog.ollama_client_factory = lambda _: client
        settings = {"backend_hosts": {"Ollama": host}}
        self.store.save_settings(settings)
        original = self.catalog.scan(settings)[0]
        self.store.rename_model(original["id"], "Моя LLM")
        renamed = self.catalog.scan(self.store.settings())[0]
        self.assertEqual(renamed["name"], "Моя LLM")
        self.assertEqual(renamed["tag"], "tag-32k:latest")
        self.assertEqual(renamed["id"], original["id"])
        self.store.rename_model(original["id"], "")
        self.assertEqual(self.catalog.scan(self.store.settings())[0]["name"], "tag-32k:latest")

    def test_scan_reappear_and_replacement_preserve_historical_identity(self):
        first = self.catalog.scan(self.settings)[0]
        self.assertEqual(first["quantization"], "Q4_K_M")
        verified = self.catalog.hash_model(first["id"])
        self.assertTrue(verified["identity_verified"])
        self.assertEqual(len(verified["digest"]), 64)
        self.store.save_result({"model_id": first["id"], "runs": []})
        self.model.unlink()
        self.assertFalse(self.catalog.scan(self.settings)[0]["available"])
        gguf(self.model)
        rows = self.catalog.scan(self.settings)
        # If the filesystem stamp changes, do not silently attach the old history.
        available = [r for r in rows if r["available"]]
        self.assertEqual(len(available), 1)
        self.assertEqual(len(self.store.results(first["id"])), 1)
        self.model.write_bytes(self.model.read_bytes() + b"replacement")
        rows = self.catalog.scan(self.settings)
        current = next(r for r in rows if r["available"])
        self.assertNotEqual(current["id"], available[0]["id"])
        self.assertFalse(current["identity_verified"])

    def test_cancel_hash_and_failed_ollama_scan_do_not_remove_prior_ollama(self):
        row = self.catalog.scan(self.settings)[0]
        flag = threading.Event(); flag.set()
        with self.assertRaises(InterruptedError):
            self.catalog.hash_model(row["id"], flag)
        self.assertFalse(self.store.models()[0]["identity_verified"])
        self.store.upsert_model({"backend": "ollama", "locator": "http://localhost:11434/a",
                                 "host": "http://localhost:11434", "tag": "a", "name": "a",
                                 "digest": "abc", "available": True})
        self.catalog.ollama_client_factory = lambda host: (_ for _ in ()).throw(RuntimeError("offline"))
        self.catalog.scan({**self.settings, "backend_hosts": {"Ollama": "http://localhost:11434"}})
        self.assertTrue(next(r for r in self.store.models() if r["backend"] == "ollama")["available"])
        self.assertTrue(self.catalog.errors)

    def test_delete_blocks_active_process_and_preserves_result(self):
        row = self.catalog.scan(self.settings)[0]
        self.store.save_result({"model_id": row["id"], "status": "complete"})
        with patch("model_studio.catalog.local_model_processes", return_value=[123]):
            with self.assertRaises(RuntimeError):
                self.catalog.delete_model(row["id"], self.settings)
        self.assertTrue(self.model.exists())
        self.assertTrue(self.store.models()[0]["available"])
        with patch("model_studio.catalog.local_model_processes", return_value=[]):
            self.catalog.delete_model(row["id"], self.settings)
        self.assertFalse(self.model.exists())
        self.assertEqual(len(self.store.results(row["id"])), 1)

    def test_digest_proven_move_relinks_installation_and_history(self):
        settings = {**self.settings, "model_aliases": {"gguf": {str(self.model.resolve()).casefold(): "Старое имя"}}}
        first = self.catalog.scan(settings)[0]
        self.catalog.hash_model(first["id"])
        self.store.save_result({"model_id": first["id"], "status": "completed"})
        moved = self.root / "другая-папка"
        moved.mkdir()
        target = moved / self.model.name
        self.model.rename(target)
        rows = self.catalog.scan(settings)
        provisional = next(r for r in rows if r["available"])
        self.assertNotEqual(provisional["id"], first["id"])
        self.assertFalse(provisional["identity_verified"])
        linked = self.catalog.hash_model(provisional["id"])
        self.assertEqual(linked["id"], first["id"])
        self.assertEqual(linked["name"], "Старое имя")
        self.assertEqual(len(self.store.models()), 1)
        self.assertEqual(len(self.store.results(first["id"])), 1)
        installs = self.store.installations()
        self.assertEqual(len(installs), 2)
        self.assertEqual(sum(i["available"] for i in installs), 1)
        self.assertEqual(self.catalog.scan(settings)[0]["id"], first["id"])

    def test_external_llama_is_discovered_without_ownership_or_deletion(self):
        class FakeLlama:
            host = "http://127.0.0.1:8081"

            def list_models(self):
                return [{"id": "loaded-model", "name": "loaded-model",
                         "meta": {"n_ctx": 8192, "size": 123}}]

        catalog = Catalog(self.store, llama_client_factory=lambda host: FakeLlama())
        settings = {**self.settings, "enable_external_llama": True,
                    "external_host": "http://127.0.0.1:8081"}
        row = next(r for r in catalog.scan(settings) if r["backend"] == "llama.cpp")
        self.assertEqual(row["context_limit"], 8192)
        self.assertEqual(row["tag"], "loaded-model")
        self.assertFalse(row["identity_verified"])
        with self.assertRaises(RuntimeError):
            catalog.delete_model(row["id"], settings)
        catalog.llama_client_factory = lambda host: (_ for _ in ()).throw(RuntimeError("offline"))
        catalog.scan(settings)
        self.assertTrue(next(r for r in self.store.models() if r["id"] == row["id"])["available"])
        self.assertTrue(catalog.errors)

    def test_mmproj_sidecar_is_present_but_not_testable(self):
        sidecar = self.root / "vision-mmproj.gguf"
        gguf(sidecar)
        rows = self.catalog.scan(self.settings)
        self.assertFalse(next(r for r in rows if r["path"] == str(sidecar.resolve()))["testable"])
        self.assertTrue(next(r for r in rows if r["path"] == str(self.model.resolve()))["testable"])
