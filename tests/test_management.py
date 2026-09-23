import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine import Cancelled, Client
from inventory import (gguf_metadata, scan_gguf, delete_gguf, fingerprint,
                       delete_ollama, load_settings, save_settings, model_labels, testable_gguf)
from managed_server import ManagedServer


def gguf(path):
    def string(value):
        data = value.encode()
        return struct.pack("<Q", len(data)) + data
    data = b"GGUF" + struct.pack("<IQQ", 3, 0, 3)
    for key, value in [("general.architecture", "test"), ("general.name", "test model")]:
        data += string(key) + struct.pack("<I", 8) + string(value)
    data += string("general.file_type") + struct.pack("<II", 4, 15)
    path.write_bytes(data)


class InventoryTests(unittest.TestCase):
    def test_gguf_labels_show_filename_and_disambiguate_duplicates(self):
        paths = [r"C:\models\bonsai.gguf", r"D:\other\bonsai.gguf", r"C:\models\other.gguf"]
        labels = model_labels(paths)
        self.assertEqual(set(labels.values()), set(paths))
        self.assertIn("other.gguf", labels)
        self.assertEqual(len(labels), 3)

    def test_projector_is_not_offered_as_a_language_model(self):
        self.assertFalse(testable_gguf({"name": "vision-mmproj.gguf", "architecture": "clip"}))
        self.assertTrue(testable_gguf({"name": "ternary-model.gguf", "architecture": "qwen35"}))
        self.assertTrue(testable_gguf({"name": "unknown.gguf", "architecture": None}))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_metadata_and_scoped_single_file_deletion(self):
        models = self.root / "models"
        models.mkdir()
        chosen, companion = models / "test.gguf", models / "mmproj.gguf"
        gguf(chosen)
        gguf(companion)
        outside = self.root / "outside.gguf"
        gguf(outside)
        rows, errors = scan_gguf([str(models)])
        self.assertFalse(errors)
        self.assertEqual(len(rows), 2)
        row = next(r for r in rows if r["path"] == str(chosen))
        self.assertEqual(row["quant"], "Q4_K_M")
        self.assertEqual(row["architecture"], "test")
        delete_gguf(row, [str(models)])
        self.assertFalse(chosen.exists())
        self.assertTrue(companion.exists())
        self.assertTrue(outside.exists())

    def test_changed_file_and_outside_root_cannot_be_deleted(self):
        path = self.root / "test.gguf"
        gguf(path)
        row = scan_gguf([str(self.root)])[0][0]
        with self.assertRaises(RuntimeError):
            delete_gguf(row, [str(self.root / "other")])
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(RuntimeError, "изменился"):
            delete_gguf(row, [str(self.root)])
        self.assertTrue(path.exists())

    def test_malformed_metadata_is_bounded_and_still_listed(self):
        path = self.root / "broken.gguf"
        path.write_bytes(b"GGUF" + struct.pack("<IQQQ", 3, 0, 1, 2**60))
        rows, errors = scan_gguf([str(self.root)])
        self.assertEqual(len(rows), 1)
        self.assertIn("error", rows[0])
        with self.assertRaises(ValueError):
            gguf_metadata(path)

    def test_settings_preserve_removed_default_folder(self):
        path = self.root / "settings.json"
        save_settings(path, {"model_dirs": [], "server_exe": "test"})
        self.assertEqual(load_settings(path)["model_dirs"], [])

    def test_ollama_unloads_then_deletes_only_selected_tag(self):
        row = {"name": "test:tag", "digest": "abc", "size": 100}
        other = {"name": "other", "digest": "def", "size": 200}
        client = Mock()
        client.list_models.side_effect = [[row, other], [other]]
        client.loaded_models.side_effect = [[row], []]
        delete_ollama(client, row, True)
        calls = client.request.call_args_list
        self.assertEqual(calls[0].args, ("/api/generate", {"model": "test:tag", "keep_alive": 0, "stream": False}))
        self.assertEqual(calls[1].args, ("/api/delete", {"model": "test:tag"}))
        self.assertEqual(calls[1].kwargs["method"], "DELETE")

    def test_newly_loaded_ollama_requires_new_confirmation(self):
        row = {"name": "test", "digest": "abc", "size": 100}
        client = Mock()
        client.list_models.return_value = [row]
        client.loaded_models.return_value = [row]
        with self.assertRaises(RuntimeError):
            delete_ollama(client, row, False)
        client.request.assert_not_called()

    def test_delete_transport_empty_success_and_shared_tag_preserved(self):
        row = {"name": "selected", "digest": "same-weights", "size": 100}
        shared = dict(row, name="shared-tag")
        models, loaded, calls = [row, shared], [row], []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, value=None):
                body = json.dumps(value).encode() if value is not None else b""
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.reply({"models": models if self.path == "/api/tags" else loaded})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, payload))
                loaded.clear()
                self.reply({"done": True})

            def do_DELETE(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, payload))
                models[:] = [m for m in models if m["name"] != payload["model"]]
                self.reply()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            delete_ollama(Client(f"http://127.0.0.1:{server.server_port}"), row, True)
            self.assertEqual(models, [shared])
            self.assertEqual(calls[-1], ("/api/delete", {"model": "selected"}))
            self.assertFalse(loaded)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class ManagedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.exe, self.model = root / "server.exe", root / "model.gguf"
        self.exe.touch()
        gguf(self.model)
        self.server = ManagedServer(root / "logs")
        self.stop = threading.Event()
        self.process = Mock()
        self.process.poll.return_value = None
        self.client = Mock()
        self.client.host = "http://127.0.0.1:8081"
        self.client.list_models.return_value = [{"name": "test"}]
        self.client.model_info = {"model_path": str(self.model), "context_limit": 65536}

    def tearDown(self):
        self.temp.cleanup()

    def run_server(self, context=65536, timeout=1):
        return self.server.ensure(str(self.exe), str(self.model), self.client.host, context,
                                  self.stop, lambda *args: None, timeout=timeout)

    def test_context_arguments_verified_and_changed_context_restarts(self):
        with patch("managed_server.LlamaCppClient", return_value=self.client), \
             patch("managed_server.socket.socket"), \
             patch("managed_server.subprocess.Popen", return_value=self.process) as launch:
            self.run_server()
            args = launch.call_args.args[0]
            self.assertEqual(args[args.index("-c") + 1], "65536")
            self.assertEqual(args[args.index("-np") + 1], "1")
            self.run_server()
            self.assertEqual(launch.call_count, 1)
            self.client.model_info["context_limit"] = 32768
            self.run_server(32768)
            self.assertEqual(launch.call_count, 2)
            self.process.terminate.assert_called_once()
            self.assertTrue(self.server.running)

    def test_actual_context_mismatch_stops_process(self):
        self.client.model_info["context_limit"] = 4096
        with patch("managed_server.LlamaCppClient", return_value=self.client), \
             patch("managed_server.socket.socket"), \
             patch("managed_server.subprocess.Popen", return_value=self.process):
            with self.assertRaisesRegex(RuntimeError, "4096"):
                self.run_server()
        self.process.terminate.assert_called_once()
        self.assertFalse(self.server.running)

    def test_timeout_and_cancel_cleanup(self):
        self.client.request.side_effect = RuntimeError("loading")
        with patch("managed_server.LlamaCppClient", return_value=self.client), \
             patch("managed_server.socket.socket"), \
             patch("managed_server.subprocess.Popen", return_value=self.process):
            with self.assertRaisesRegex(RuntimeError, "не запустился"):
                self.run_server(timeout=.01)
            self.assertFalse(self.server.running)
            self.stop.set()
            with self.assertRaises(Cancelled):
                self.run_server()

    def test_crash_cleanup_and_readable_error(self):
        self.process.poll.return_value = 1
        with patch("managed_server.LlamaCppClient", return_value=self.client), \
             patch("managed_server.socket.socket"), \
             patch("managed_server.subprocess.Popen", return_value=self.process):
            with self.assertRaisesRegex(RuntimeError, "недостаточно памяти"):
                self.run_server()
            self.assertFalse(self.server.running)

    def test_occupied_port_never_stops_external_server(self):
        with patch("managed_server.LlamaCppClient", return_value=self.client), \
             patch("managed_server.socket.socket") as sock, \
             patch("managed_server.subprocess.Popen") as launch:
            sock.return_value.__enter__.return_value.bind.side_effect = OSError("busy")
            with self.assertRaisesRegex(RuntimeError, "занят"):
                self.run_server()
            launch.assert_not_called()
            self.assertFalse(self.server.running)

    def test_cancel_during_startup_stops_owned_process(self):
        def cancel(*args, **kwargs):
            self.stop.set()
            raise RuntimeError("loading")
        self.client.request.side_effect = cancel
        with patch("managed_server.LlamaCppClient", return_value=self.client), \
             patch("managed_server.socket.socket"), \
             patch("managed_server.subprocess.Popen", return_value=self.process):
            with self.assertRaises(Cancelled):
                self.run_server()
            self.process.terminate.assert_called_once()
            self.assertFalse(self.server.running)


if __name__ == "__main__":
    unittest.main()
