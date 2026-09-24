"""Vision transport and saved projector identity contracts."""

import base64
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_studio.backends.llama_cpp import LlamaCppBackend
from model_studio.backends.ollama import OllamaBackend
from model_studio.benchmarks.recommendations import recommendations
from model_studio.benchmarks.reports import report_text
from model_studio.benchmarks.research import prepare_result
from model_studio.configuration import LaunchConfig, projector_identity
from model_studio.storage import Store


IMAGE = base64.b64encode(b"\x89PNG\r\n\x1a\nunit-test").decode("ascii")
MESSAGES = [{"role": "user", "content": "Describe this", "images": [IMAGE]}]


class VisionTests(unittest.TestCase):
    def test_transport_shapes_are_protocol_specific(self):
        sent = {}

        def llama_stream(path, payload, stop, sse=False):
            sent["llama"] = payload
            yield {"_stream_done": True}, .01

        llama = LlamaCppBackend.__new__(LlamaCppBackend)
        llama.client = SimpleNamespace(stream=llama_stream)
        list(llama.chat("model", MESSAGES, 10, .7, threading.Event()))
        content = sent["llama"]["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Describe this"})
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64," + IMAGE)

        def ollama_stream(path, payload, stop):
            sent["ollama"] = payload
            yield {"done": True}, .01

        ollama = OllamaBackend.__new__(OllamaBackend)
        ollama.client = SimpleNamespace(stream=ollama_stream, context=4096,
                                        keep_alive=-1, reasoning="auto")
        list(ollama.chat("tag", MESSAGES, 10, .7, threading.Event()))
        self.assertEqual(sent["ollama"]["messages"][0], MESSAGES[0])

    def test_projector_identity_keeps_changed_module_out_of_recommendations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            module = root / "mmproj.gguf"
            module.write_bytes(b"projector-v1")
            selected = projector_identity(str(module))
            store = Store(root / "store.db")
            model = store.upsert_model({"backend": "gguf", "locator": "model.gguf",
                                        "digest": "model-digest", "identity_verified": True})
            config = LaunchConfig(model="model.gguf", executable="llama-server.exe",
                                  context=4096, model_id=model["id"], mmproj=str(module))
            measured = {"status": "completed", "requested_runs": 1,
                        "requested_tokens_per_run": 8,
                        "placement_after_warmup": {"context_length": 4096},
                        "runs": [{"index": 1, "tokens": 8, "generation_seconds": 1,
                                  "tokens_per_second": 8}],
                        "summary": {"median_tokens_per_second": 8}}
            client = SimpleNamespace(projector_identity=selected, vision_available=True)
            environment = {"backend": "llama.cpp", "runtime_build": "v1",
                           "hardware": "GPU", "driver": "D1", "verified": True,
                           "projector_digest": selected["digest"], "projector_verified": True,
                           "projector_size_bytes": selected["size_bytes"],
                           "projector_mtime_ns": str(selected["mtime_ns"])}
            with patch("model_studio.benchmarks.research.environment_snapshot", return_value=environment):
                result = prepare_result(store, config, measured, client=client)
            self.assertTrue(result["comparison_eligible"])
            self.assertEqual(result["artifact"]["projector"]["digest"], selected["digest"])
            self.assertNotIn(str(root), report_text(result))
            result["id"] = "before"
            self.assertTrue(recommendations([result], config.to_dict(),
                                            current_environment=environment)[0]["available"])
            module.write_bytes(b"projector-v2")
            changed = projector_identity(str(module))
            with patch("model_studio.benchmarks.research.environment_snapshot", return_value={
                    **environment, "projector_digest": changed["digest"]}):
                stale = prepare_result(store, config, measured, client=client)
            self.assertFalse(stale["comparison_eligible"])
            self.assertIn("Vision projector identity or active attachment is unverified",
                          stale["blocking_reasons"])
            self.assertFalse(recommendations([result], config.to_dict(),
                                             current_environment=environment)[0]["available"])
            self.assertFalse(recommendations([result], config.to_dict(),
                                             current_environment={**environment,
                                                                  "projector_digest": changed["digest"]})[0]["available"])


if __name__ == "__main__":
    unittest.main()
