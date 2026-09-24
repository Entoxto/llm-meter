"""Managed image references and portable backups, without a model request."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_studio.attachments import AttachmentError, import_image, load_image, reference
from model_studio.storage.store import Store


def png_pixel() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (len(payload).to_bytes(4, "big") + kind + payload
                + (zlib.crc32(kind + payload) & 0xffffffff).to_bytes(4, "big"))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00")
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
            + chunk(b"IEND", b""))


class AttachmentTests(unittest.TestCase):
    def test_import_validates_content_and_preserves_external_file(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            source = root / "named.jpeg"
            raw = png_pixel()
            source.write_bytes(raw)
            item = import_image(source, root / "data")
            self.assertEqual(item["mime"], "image/png")
            self.assertEqual(item["name"], "named.jpeg")
            self.assertEqual(Path(item["path"]).suffix, ".png")
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(reference(item)["path"], f"attachments/{item['id']}.png")
            self.assertTrue(load_image(reference(item), root / "data"))
            (root / "fake.webp").write_bytes(b"RIFFxxxxWEBP")
            with self.assertRaisesRegex(AttachmentError, "PNG и JPEG"):
                import_image(root / "fake.webp", root / "data")
            (root / "broken.png").write_bytes(raw[:-3])
            with self.assertRaises(AttachmentError):
                import_image(root / "broken.png", root / "data")

    def test_backup_roundtrip_relocates_image_references(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            first, second = root / "first", root / "second"
            first.mkdir(); second.mkdir()
            source = root / "source.png"
            source.write_bytes(png_pixel())
            image = import_image(source, first)
            original = Store(first / "studio.db")
            conversation = original.create_conversation("Picture")
            original.save_message(conversation["id"], "user", "What is this?",
                                  metadata={"attachments": [reference(image)]})
            bundle = original.backup(root / "portable.studio-backup")
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(set(archive.namelist()),
                                 {"studio.db", reference(image)["path"]})
            restored = Store(second / "studio.db")
            old_image = import_image(source, second)
            old_conversation = restored.create_conversation("Before restore")
            restored.save_message(old_conversation["id"], "user", "Old",
                                  metadata={"attachments": [reference(old_image)]})
            preserved = restored.restore_backup(bundle)
            self.assertEqual(preserved.suffix, ".studio-backup")
            with zipfile.ZipFile(preserved) as archive:
                self.assertIn(reference(old_image)["path"], archive.namelist())
            row = restored.messages(conversation["id"])[0]
            ref = row["metadata"]["attachments"][0]
            self.assertEqual(ref["path"], reference(image)["path"])
            self.assertTrue(load_image(ref, second))
            self.assertTrue((second / ref["path"]).is_file())
            self.assertFalse((second / reference(old_image)["path"]).exists())

    def test_restore_rejects_zip_traversal_before_replacing_database(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            store = Store(root / "data" / "studio.db")
            conversation = store.create_conversation("Keep")
            plain = store.backup(root / "plain.db")
            malicious = root / "malicious.studio-backup"
            with zipfile.ZipFile(malicious, "w") as archive:
                archive.write(plain, "studio.db")
                archive.writestr("../escaped.png", png_pixel())
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                store.restore_backup(malicious)
            self.assertFalse((root / "escaped.png").exists())
            self.assertEqual(store.conversations()[0]["id"], conversation["id"])


if __name__ == "__main__":
    unittest.main()
