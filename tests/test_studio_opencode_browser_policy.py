import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_studio.integrations.opencode_browser_policy import MARKER, grant_browser, revoke_browser


class BrowserPolicyTests(unittest.TestCase):
    def test_grant_preserves_user_deny_and_original_session(self):
        original = {"permissions": [{"action": "browser", "resource": "*", "effect": "deny"}],
                    "metadata": {"user": "value"}}
        granted = grant_browser(original, "host-1")
        self.assertEqual(granted["permissions"][0],
                         {"action": "browser", "resource": "*", "effect": "allow"})
        self.assertEqual(granted["permissions"][-1], original["permissions"][0])
        self.assertEqual(granted["metadata"][MARKER], "host-1")
        self.assertNotIn(MARKER, original["metadata"])
        self.assertEqual(revoke_browser(granted, "host-1"), original)

    def test_replacement_and_late_detach_do_not_revoke_new_host(self):
        first = grant_browser({}, "host-1")
        second = grant_browser(first, "host-2")
        self.assertEqual(len(second["permissions"]), 1)
        self.assertIsNone(revoke_browser(second, "host-1"))
        self.assertEqual(revoke_browser(second, "host-2"), {"permissions": [], "metadata": {}})

    def test_startup_cleanup_removes_prior_studio_rule(self):
        stale = grant_browser({"permissions": [{"action": "shell", "resource": "*", "effect": "ask"}]},
                              "old-process")
        cleaned = revoke_browser(stale)
        self.assertEqual(cleaned["permissions"], [{"action": "shell", "resource": "*", "effect": "ask"}])
        self.assertIsNone(revoke_browser(cleaned))


if __name__ == "__main__":
    unittest.main()
