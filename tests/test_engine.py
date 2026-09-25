import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Cancelled, Client, CONTEXT, GIB, normalize_host, placement, run_benchmark, gpu_summary
from llama_cpp import LlamaCppClient, log_memory


class StreamDeadlineTests(unittest.TestCase):
    def test_default_and_extended_timeout_keep_cancellation(self):
        from engine import InterruptibleReader
        sock = Mock()
        sock.recv_into.return_value = 1
        stop = threading.Event()
        cancelled = threading.Event()
        default = InterruptibleReader(sock, stop, cancelled, 0)
        extended = InterruptibleReader(sock, stop, cancelled, 0, timeout=900)
        self.addCleanup(default.close)
        self.addCleanup(extended.close)
        with patch("engine.time.perf_counter", return_value=301):
            with self.assertRaisesRegex(RuntimeError, "300"):
                default.readinto(bytearray(1))
            self.assertEqual(extended.readinto(bytearray(1)), 1)
            stop.set()
            with self.assertRaises(Cancelled):
                extended.readinto(bytearray(1))


class Fixture:
    def __init__(self):
        self.requests = []
        self.context = CONTEXT
        self.mode = "normal"
        self.connected = threading.Event()
        self.release = threading.Event()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def send_json(self, data, status=200):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/version":
                    self.send_json({"version": "fixture"})
                elif self.path == "/api/tags":
                    self.send_json({"models": [{"name": "test:latest", "digest": "abc", "size": 8 * GIB}]})
                elif self.path == "/api/ps":
                    self.send_json({"models": [{"name": "test:latest", "digest": "abc",
                        "size": 10 * GIB, "size_vram": 6 * GIB, "context_length": fixture.context}]})
                elif self.path == "/v1/models":
                    self.send_json({"data": [{"id": "test:latest", "meta": {
                        "size": 8 * GIB, "n_ctx_train": 131072, "ftype": "Q4_K_M"}}]})
                elif self.path.startswith("/props"):
                    self.send_json({"default_generation_settings": {"n_ctx": fixture.context},
                                    "model_path": "C:/models/test.gguf", "build_info": "fixture"})
                else:
                    self.send_json({"error": "missing"}, 404)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.requests.append((self.path, payload))
                if self.path == "/api/show":
                    self.send_json({"capabilities": ["completion"], "details": {"quantization_level": "Q4_K_M"}})
                    return
                if fixture.mode == "http_error":
                    self.send_json({"error": "out of memory"}, 500)
                    return
                self.send_response(200)
                self.send_header("Connection", "close")
                self.end_headers()
                fixture.connected.set()
                if fixture.mode == "stall":
                    fixture.release.wait(5)
                    return
                if self.path == "/v1/chat/completions":
                    count = payload["max_tokens"]
                    events = [
                        {"choices": [{"delta": {"role": "assistant"}}]},
                        {"choices": [{"delta": {"reasoning_content": "thinking"}}]},
                        {"choices": [{"delta": {"content": "answer"}}]},
                        {"choices": [{"delta": {}, "finish_reason": "length"}]},
                        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": count,
                            "prompt_tokens_details": {"cached_tokens": 20}},
                         "timings": {"predicted_n": count, "predicted_ms": 2000,
                                     "prompt_n": 80, "prompt_ms": 500, "cache_n": 20}}]
                    if fixture.mode == "no_timings":
                        events[-1].pop("timings")
                    if fixture.mode == "mtp":
                        events[-1]["timings"].update(draft_n=350, draft_n_accepted=225)
                    # Include comments and split one JSON event over multiple SSE data lines.
                    self.wfile.write(b': heartbeat\r\n\r\n')
                    for event in events:
                        data = json.dumps(event).encode()
                        if 'usage' in event:
                            data = data.replace(b', "timings"', b',\r\ndata: "timings"')
                        self.wfile.write(b'data: ' + data + b'\r\n\r\n')
                    if fixture.mode != "truncated":
                        self.wfile.write(b'data: [DONE]\r\n\r\n')
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(b'{"response":"one chunk has many words","done":false}\n')
                if fixture.mode != "truncated":
                    count = payload["options"]["num_predict"]
                    if fixture.mode == "early":
                        count = 3
                    self.wfile.write(json.dumps({"done": True, "eval_count": count,
                        "eval_duration": 2_000_000_000, "load_duration": 8_000_000_000,
                        "prompt_eval_count": 100, "prompt_eval_cached_count": 20,
                        "prompt_eval_duration": 1_000_000_000}).encode() + b"\n")
                self.wfile.flush()
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()
        self.client = Client(f"http://127.0.0.1:{self.server.server_port}")

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)

    def test_speed_uses_server_tokens_and_excludes_load(self):
        result = self.fixture.client.generate("test:latest", "text", 512, threading.Event())
        self.assertEqual(result["tokens_per_second"], 256)
        self.assertEqual(result["generation_seconds"], 2)
        self.assertEqual(result["prompt_tokens"], 100)
        self.assertEqual(result["prompt_processed_tokens"], 80)
        self.assertEqual(result["prompt_tokens_per_second"], 80)
        self.assertIsNotNone(result["first_output_seconds"])
        self.assertEqual(self.fixture.requests[-1][1]["options"]["num_ctx"], 32768)

    def test_truncated_stream_is_not_a_success(self):
        self.fixture.mode = "truncated"
        with self.assertRaisesRegex(RuntimeError, "оборвался"):
            self.fixture.client.generate("test", "text", 512, threading.Event())

    def test_server_error_is_readable(self):
        self.fixture.mode = "http_error"
        with self.assertRaisesRegex(RuntimeError, "out of memory"):
            self.fixture.client.generate("test", "text", 512, threading.Event())

    def test_cancel_interrupts_wait_for_first_token(self):
        self.fixture.mode = "stall"
        stop = threading.Event()
        errors = []

        def request():
            try:
                self.fixture.client.generate("test", "text", 512, stop)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        self.assertTrue(self.fixture.connected.wait(2))
        stop.set()
        self.fixture.client.cancel()
        worker.join(2)
        self.assertFalse(worker.is_alive(), "Cancel must interrupt a stalled socket")
        self.assertIsInstance(errors[0], Cancelled)

    def run_fixture(self, client=None):
        sample = {"models": [], "gpus": [], "ram": None, "errors": []}
        with tempfile.TemporaryDirectory() as folder, patch("engine.Telemetry.snapshot", return_value=sample):
            report = run_benchmark(client or self.fixture.client, "test:latest", lambda *args: None,
                                   threading.Event(), folder)
            saved = json.loads(Path(report["saved_to"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], report["status"])
            return report

    def test_three_runs_and_warmup_excluded(self):
        report = self.run_fixture()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(report["runs"]), 3)
        self.assertEqual(report["summary"]["median_tokens_per_second"], 256)
        self.assertEqual(report["summary"]["aggregate_tokens_per_second"], 256)
        self.assertEqual(report["warmup"]["tokens"], 16)

    def test_context_mismatch_rejects_benchmark(self):
        self.fixture.context = 4096
        report = self.run_fixture()
        self.assertEqual(report["status"], "error")
        self.assertNotIn("summary", report)
        self.assertIn("4096", report["error"])

    def test_early_stop_uses_actual_count_and_warns(self):
        self.fixture.mode = "early"
        report = self.run_fixture()
        self.assertEqual(report["summary"]["median_tokens_per_second"], 1.5)
        self.assertEqual(len(report["warnings"]), 3)

    def test_memory_accounting_and_missing_data(self):
        self.assertEqual(placement({"size": 10 * GIB, "size_vram": 6 * GIB}),
                         {"vram_bytes": 6 * GIB, "ram_estimate_bytes": 4 * GIB, "gpu_percent": 60})
        self.assertEqual(placement({"size": 4 * GIB, "size_vram": 0})["gpu_percent"], 0)
        self.assertEqual(placement({"size": 4 * GIB, "size_vram": 4 * GIB})["ram_estimate_bytes"], 0)
        self.assertIsNone(placement({"size": 4 * GIB})["ram_estimate_bytes"])

    def test_host_defaults(self):
        self.assertEqual(normalize_host("localhost"), "http://localhost:11434")
        self.assertEqual(normalize_host("0.0.0.0:11434"), "http://127.0.0.1:11434")
        self.assertEqual(normalize_host("https://example.com"), "https://example.com:443")
        self.assertFalse(Client("https://example.com").local)

    def test_llama_stream_and_matching_metric_names(self):
        client = LlamaCppClient(self.fixture.client.host + "/v1")
        result = client.generate("test:latest", "text", 512, threading.Event())
        self.assertEqual(result["output_tokens"], 512)
        self.assertEqual(result["tokens_per_second"], 256)
        self.assertEqual(result["prompt_tokens_per_second"], 160)
        self.assertEqual(result["prompt_tokens"], 100)
        self.assertEqual(result["prompt_processed_tokens"], 80)
        self.assertEqual(result["prompt_cached_tokens"], 20)
        self.assertIsNotNone(result["ttft_seconds"])
        self.assertTrue(result["thinking_seen"])
        payload = self.fixture.requests[-1][1]
        self.assertFalse(payload["cache_prompt"])
        self.assertNotIn("num_ctx", payload)
        self.assertEqual(normalize_host("localhost", 8080), "http://localhost:8080")

    def test_llama_context_is_runtime_not_training_limit(self):
        self.fixture.context = 4096
        report = self.run_fixture(LlamaCppClient(self.fixture.client.host, context=4096))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["model_info"]["context_limit"], 4096)
        self.assertEqual(report["model_info"]["training_context_limit"], 131072)
        self.assertEqual(report["model_info"]["quantization"], "Q4_K_M")
        self.assertEqual(report["model_info"]["model_size_bytes"], 8 * GIB)
        self.assertIsNone(report["model_info"]["vram_bytes"])
        self.assertEqual(len(report["runs"]), 3)
        self.assertEqual(report["requested_context"], 4096)

    def test_llama_external_context_mismatch_stops_before_generation(self):
        self.fixture.context = 4096
        report = self.run_fixture(LlamaCppClient(self.fixture.client.host, context=65536))
        self.assertEqual(report["status"], "error")
        self.assertIn("65536", report["error"])
        self.assertNotIn("warmup", report)
        self.assertEqual(report["runs"], [])

    def test_managed_llama_small_upward_context_rounding_is_opt_in(self):
        self.fixture.context = 100096
        external = LlamaCppClient(self.fixture.client.host, context=100000)
        self.assertEqual(self.run_fixture(external)["status"], "error")
        managed = LlamaCppClient(self.fixture.client.host, context=100000)
        managed.context_rounding_tolerance = 255
        report = self.run_fixture(managed)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["placement_after_warmup"]["context_length"], 100096)

    def test_ollama_selected_context_sent_to_every_request(self):
        self.fixture.context = 65536
        client = Client(self.fixture.client.host, context=65536)
        report = self.run_fixture(client)
        self.assertEqual(report["status"], "completed")
        payloads = [v for p, v in self.fixture.requests if p == "/api/generate"]
        self.assertEqual(len(payloads), 4)
        self.assertTrue(all(p["options"]["num_ctx"] == 65536 for p in payloads))
        self.assertTrue(all(r["context_limit"] == 65536 for r in report["runs"]))

    def test_llama_truncated_stream_is_not_success(self):
        self.fixture.mode = "truncated"
        with self.assertRaisesRegex(RuntimeError, "оборвался"):
            LlamaCppClient(self.fixture.client.host).generate("test", "text", 512, threading.Event())

    def test_llama_requires_server_timings(self):
        self.fixture.mode = "no_timings"
        with self.assertRaisesRegex(RuntimeError, "timings"):
            LlamaCppClient(self.fixture.client.host).generate("test", "text", 512, threading.Event())

    def test_mtp_profile_requires_actual_draft_counters(self):
        client = LlamaCppClient(self.fixture.client.host)
        client.runtime_profile = "Prism MTP"
        client.required_capabilities = ("mtp",)
        with self.assertRaisesRegex(RuntimeError, "MTP не подтверждён"):
            client.generate("test", "text", 512, threading.Event())
        self.fixture.mode = "mtp"
        result = client.generate("test", "text", 512, threading.Event())
        self.assertEqual((result["draft_n"], result["draft_n_accepted"]), (350, 225))
        self.assertTrue(client.mtp_verified)

    def test_llama_cancel_before_first_sse_token(self):
        self.fixture.client = LlamaCppClient(self.fixture.client.host)
        self.test_cancel_interrupts_wait_for_first_token()

    def test_gpu_average_peak_and_missing_samples(self):
        samples = [{"gpus": [{"index": "0", "name": "GPU", "utilization_percent": value,
                              "used_bytes": 2 * GIB}]} for value in [10, None, 50, 90]]
        result = gpu_summary(samples)[0]
        self.assertEqual(result["mean_utilization_percent"], 50)
        self.assertEqual(result["peak_utilization_percent"], 90)
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(gpu_summary([]), [])

    def test_log_memory_excludes_old_runs_and_host_is_ram(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "server.log"
            path.write_text(
                'llama_model_loader: loaded meta data from C:/models/old.gguf (version GGUF V3)\n'
                'load_tensors: CUDA0 model buffer size = 999 MiB\n'
                'llama_model_loader: loaded meta data from C:/models/test.gguf (version GGUF V3)\n'
                'load_tensors: CUDA0 model buffer size = 1024 MiB\n'
                'load_tensors: CUDA_Host model buffer size = 2 MiB\n'
                'load_tensors: CPU_Mapped model buffer size = 512 MiB\n'
                'load_tensors: offloaded 10/20 layers to GPU\n', encoding="utf-8")
            result = log_memory(path, "C:/models/test.gguf")
            self.assertEqual(result["vram_bytes"], GIB)
            self.assertEqual(result["ram_estimate_bytes"], 514 * 1024 ** 2)
            self.assertEqual(result["offload_layers"], 10)
            self.assertIsNone(result["gpu_percent"])
            with self.assertRaises(ValueError):
                log_memory(path, "C:/models/wrong.gguf")


if __name__ == "__main__":
    unittest.main()
