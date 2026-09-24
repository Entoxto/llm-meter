import unittest

from model_studio.benchmarks.reports import reports_text


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
