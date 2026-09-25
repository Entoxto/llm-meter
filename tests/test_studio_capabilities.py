import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from model_studio.backends.capabilities import _probe, runtime_capabilities


class RuntimeCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        _probe.cache_clear()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.exe = Path(temp.name) / "server.exe"
        self.exe.write_bytes(b"fixture")

    def test_exact_flags_cached_until_executable_changes(self):
        result = Mock(returncode=0, stdout="--reasoning [on|off|auto]\n--reasoning-budget N\n--cache-type-k TYPE\n--cache-type-v TYPE", stderr="")
        with patch("model_studio.backends.capabilities.subprocess.run", return_value=result) as run:
            self.assertEqual(runtime_capabilities(str(self.exe)), ["reasoning", "reasoning-budget", "kv-cache"])
            runtime_capabilities(str(self.exe))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], [str(self.exe), "--help"])
            self.assertEqual(run.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.exe.write_bytes(b"replaced fixture")
            runtime_capabilities(str(self.exe))
            self.assertEqual(run.call_count, 2)

    def test_related_flags_or_failed_help_do_not_enable_controls(self):
        for result in (Mock(returncode=0, stdout="--reasoning-format FORMAT\n--reasoning-budget N\n--cache-type-k-draft TYPE\n--cache-type-v TYPE", stderr=""),
                       Mock(returncode=1, stdout="--reasoning [on|off|auto]", stderr="")):
            _probe.cache_clear()
            with patch("model_studio.backends.capabilities.subprocess.run", return_value=result):
                self.assertEqual(runtime_capabilities(str(self.exe)), [])
        _probe.cache_clear()
        with patch("model_studio.backends.capabilities.subprocess.run", side_effect=subprocess.TimeoutExpired("server", 5)):
            self.assertEqual(runtime_capabilities(str(self.exe)), [])

    def test_reasoning_without_budget_does_not_enable_numeric_limits(self):
        result = Mock(returncode=0, stdout="--reasoning [on|off|auto]\n--reasoning-budget-message TEXT", stderr="")
        with patch("model_studio.backends.capabilities.subprocess.run", return_value=result):
            self.assertEqual(runtime_capabilities(str(self.exe)), ["reasoning"])

    def test_mtp_runtime_requires_exact_selector_and_draft_limit(self):
        result = Mock(returncode=0,
                      stdout="--spec-type none,draft-simple,draft-mtp\n--spec-draft-n-max N",
                      stderr="")
        with patch("model_studio.backends.capabilities.subprocess.run", return_value=result):
            self.assertEqual(runtime_capabilities(str(self.exe)), ["mtp-runtime"])
        for help_text in ("--spec-type none,draft-mtp\n--spec-draft-n-min N",
                          "--spec-type-extra draft-mtp\n--spec-draft-n-max N",
                          "--spec-type none,draft-mtp-other\n--spec-draft-n-max N"):
            with self.subTest(help_text=help_text):
                _probe.cache_clear()
                result.stdout = help_text
                with patch("model_studio.backends.capabilities.subprocess.run", return_value=result):
                    self.assertEqual(runtime_capabilities(str(self.exe)), [])
