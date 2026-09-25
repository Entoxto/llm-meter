"""Static MTP evidence must come from the GGUF directory, not its name."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inventory import gguf_metadata, scan_gguf
from model_studio.catalog import Catalog
from model_studio.storage import Store


def _string(value):
    raw = value.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _gguf(path, *, architecture="qwen35", head=True, omit=None):
    metadata = [("general.architecture", 8, architecture),
                (f"{architecture}.block_count", 4, 2),
                (f"{architecture}.nextn_predict_layers", 4, 1)]
    names = []
    if head:
        names = ["blk.1." + suffix for suffix in (
            "attn_norm.weight", "post_attention_norm.weight", "attn_output.weight",
            "attn_q_norm.weight", "attn_k_norm.weight", "attn_q.weight",
            "attn_k.weight", "attn_v.weight", "ffn_gate.weight", "ffn_down.weight",
            "ffn_up.weight", "nextn.eh_proj.weight", "nextn.enorm.weight",
            "nextn.hnorm.weight") if suffix != omit]
    data = b"GGUF" + struct.pack("<IQQ", 3, len(names), len(metadata))
    for key, kind, value in metadata:
        data += _string(key) + struct.pack("<I", kind)
        data += _string(value) if kind == 8 else struct.pack("<I", value)
    for name in names:
        data += _string(name) + struct.pack("<IQIQ", 1, 1, 0, 0)
    path.write_bytes(data)


class MtpInventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_supported_qwen35_head_is_persisted_as_model_evidence(self):
        path = self.root / "ordinary-name.gguf"
        _gguf(path)
        metadata = gguf_metadata(path)
        self.assertEqual(metadata["mtp_model_status"], "supported")
        self.assertEqual(metadata["mtp_model_evidence"]["head_block"], 1)
        self.assertTrue(metadata["mtp_model_evidence"]["required_tensors_present"])
        store = Store(self.root / "studio.db")
        row = Catalog(store).scan({"model_dirs": [str(self.root)]})[0]
        self.assertEqual(row["mtp_model_status"], "supported")
        self.assertEqual(store.models()[0]["mtp_model_evidence"], metadata["mtp_model_evidence"])

    def test_metadata_without_head_and_missing_required_tensor_are_unsupported(self):
        for suffix, options in (("metadata", {"head": False}),
                                ("incomplete", {"omit": "nextn.eh_proj.weight"})):
            with self.subTest(suffix=suffix):
                path = self.root / f"MTP-{suffix}.gguf"
                _gguf(path, **options)
                result = gguf_metadata(path)
                self.assertEqual(result["mtp_model_status"], "unsupported")
                self.assertFalse(result["mtp_model_evidence"]["required_tensors_present"])

    def test_unknown_architecture_does_not_inherit_qwen35_tensor_rule(self):
        path = self.root / "other.gguf"
        _gguf(path, architecture="other")
        self.assertEqual(gguf_metadata(path)["mtp_model_status"], "unknown")

    def test_malformed_directory_stays_unknown_in_scan(self):
        path = self.root / "broken.gguf"
        _gguf(path)
        path.write_bytes(path.read_bytes()[:-8])
        row = scan_gguf([str(self.root)])[0][0]
        self.assertEqual(row.get("mtp_model_status", "unknown"), "unknown")
        self.assertIn("error", row)
