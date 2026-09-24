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
        result = Mock(returncode=0, stdout="--reasoning [on|off|auto]\n--cache-type-k TYPE\n--cache-type-v TYPE", stderr="")
        with patch("model_studio.backends.capabilities.subprocess.run", return_value=result) as run:
            self.assertEqual(runtime_capabilities(str(self.exe)), ["reasoning", "kv-cache"])
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
