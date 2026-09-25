import unittest

from model_studio.benchmarks.reports import model_report_text, reports_text


class BulkReportTests(unittest.TestCase):
    def test_empty_failed_research_is_a_diagnostic_report(self):
        report = reports_text([], "Исследование", {"id": "job", "status": "stopped",
            "error": "Server failed at C:/private/model.gguf", "plan": {
                "base_config": {"model": "C:/private/model.gguf", "context": 100000}}})
        self.assertIn("Сохранённых замеров: 0", report)
        self.assertIn("Server failed", report)
        self.assertIn("100000", report)
        self.assertNotIn("C:/private", report)

    def test_full_report_keeps_each_result_and_marks_missing_and_running(self):
        results = [{"id": "one", "status": "completed", "config": {"context": 100000},
                    "effective_config": {"context": 100096},
                    "summary": {"median_tokens_per_second": 79.66331}},
                   {"id": "two", "status": "error", "error": "OOM"}]
        report = reports_text(results, "Bonsai", {"status": "running",
            "completed_steps": [{"result_id": "one"}, {"result_id": "missing"}]})
        self.assertIn("100000 | 100096 | 79.663", report)
        self.assertIn("Замер 2 из 2", report)
        self.assertIn("OOM", report)
        self.assertIn("Недоступно сохранённых замеров из задания: 1", report)
        self.assertIn("Исследование ещё идёт", report)

    def test_model_overview_keeps_latest_success_per_condition_and_newer_failure(self):
        def result(identifier, date, *, status="completed", context=32768, mtp=False,
                   draft=2, runtime="build-a", method="short", error=None):
            return {"id": identifier, "created_at": date, "status": status,
                    "config": {"context": context, "kv_type": "f16", "mtp": mtp,
                               "draft": draft, "reasoning": "auto", "gpu_layers": 99,
                               "runtime_name": "friendly", "capabilities": ["mtp"]},
                    "effective_config": {"context": context, "kv_type": "f16", "mtp": mtp,
                                         "draft": draft, "reasoning": "auto", "gpu_layers": 99},
                    "effective_config_verified": True,
                    "artifact": {"digest": "model-digest", "identity_verified": True},
                    "environment": {"verified": True, "runtime_build": runtime,
                                    "hardware": "GPU-A", "driver": "1"},
                    "workload": {"method": method, "signature": method + "-signature"},
                    "summary": {"median_tokens_per_second": 12.5}, "error": error}
        rows = [result("old", "2026-09-24T10:00:00Z"),
                result("new", "2026-09-25T10:00:00Z", draft=4),
                result("mtp", "2026-09-25T11:00:00Z", mtp=True, draft=2),
                result("other-runtime", "2026-09-25T12:00:00Z", runtime="build-b"),
                result("other-method", "2026-09-25T13:00:00Z", method="long"),
                result("failed", "2026-09-25T14:00:00Z", status="error", error="OOM")]
        before = repr(rows)
        report = model_report_text(rows, "Qwen")
        overview = report.split("Полная история всех сохранённых результатов:")[0]
        self.assertNotIn("old | ", overview)  # Off-MTP draft length is inactive.
        self.assertIn("new | ", overview)
        for identifier in ("mtp", "other-runtime", "other-method", "failed"):
            self.assertIn(identifier + " | ", overview)
        self.assertIn("OOM", overview)
        for identifier in ("old", "new", "mtp", "other-runtime", "other-method", "failed"):
            self.assertIn("ID: " + identifier, report)
        self.assertEqual(repr(rows), before)

    def test_unknown_provenance_remains_separate_and_paths_are_private(self):
        rows = [{"id": "first", "created_at": "2026-09-24T10:00:00Z",
                 "status": "completed", "config": {"context": 32768,
                    "model": "C:/private/model.gguf", "mtp": False},
                 "summary": {"median_tokens_per_second": 10}},
                {"id": "second", "created_at": "2026-09-25T10:00:00Z",
                 "status": "completed", "config": {"context": 32768,
                    "model": "C:/private/model.gguf", "mtp": False},
                 "summary": {"median_tokens_per_second": 11}}]
        report = model_report_text(rows, "C:/private/model.gguf")
        overview = report.split("Полная история всех сохранённых результатов:")[0]
        self.assertIn("first | ", overview)
        self.assertIn("second | ", overview)
        self.assertIn("неизвестно", overview)
        self.assertNotIn("C:/private", report)

    def test_completed_but_ineligible_result_is_marked_and_scope_is_counted(self):
        base = {"config": {"context": 32768, "kv_type": "f16", "mtp": False},
                "effective_config": {"context": 32800},
                "effective_config_verified": True,
                "artifact": {"digest": "digest", "identity_verified": True},
                "environment": {"verified": True, "runtime_build": "build",
                                "hardware": "gpu", "driver": "driver"},
                "workload": {"method": "short", "signature": "same"},
                "summary": {"median_tokens_per_second": 10}}
        old = {**base, "id": "eligible-old", "created_at": "2026-09-24T10:00:00Z",
               "status": "completed", "comparison_eligible": True,
               "long_context": {"validated": True}, "research_id": "research-1"}
        new = {**base, "id": "ineligible-new", "created_at": "2026-09-25T10:00:00Z",
               "status": "completed", "comparison_eligible": False,
               "long_context": {"validated": False}, "research_id": "research-2"}
        report = model_report_text([old, new], "model")
        overview = report.split("Полная история всех сохранённых результатов:")[0]
        self.assertIn("Сохранённых тестов: 2; разных исследований: 2", overview)
        self.assertIn("Запрошенный контекст | Фактический контекст", overview)
        self.assertIn("ineligible-new | 2026-09-25T10:00:00Z | 32768 | 32800", overview)
        self.assertIn(" | нет | нет", overview)
        self.assertNotIn("eligible-old | ", overview)
        self.assertIn("ID: eligible-old", report)
