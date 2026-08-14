from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.sample_dom import assert_on_pending_list  # noqa: E402


class PendingTabReadinessTests(unittest.TestCase):
    @patch("lib.sample_dom.time.sleep")
    @patch("lib.sample_dom.zclaw_exec")
    def test_retries_when_sample_page_tab_dom_is_still_loading(
        self,
        execute_script,
        sleep,
    ) -> None:
        execute_script.side_effect = [
            {
                "ok": False,
                "reason": "no-pending-tab",
                "href": "https://example.test/sample-request",
            },
            {
                "ok": True,
                "already": True,
                "href": "https://example.test/sample-request",
            },
        ]

        result = assert_on_pending_list("store-test", page_wait=1.5)

        self.assertTrue(result["ok"])
        self.assertEqual(execute_script.call_count, 2)
        sleep.assert_called_once_with(1.5)

    @patch("lib.sample_dom.zclaw_exec")
    def test_does_not_retry_when_not_on_sample_page(self, execute_script) -> None:
        execute_script.return_value = {
            "ok": False,
            "reason": "not-on-sample-page",
            "href": "https://example.test/home",
        }

        with self.assertRaises(RuntimeError):
            assert_on_pending_list("store-test", page_wait=0, retries=2)

        self.assertEqual(execute_script.call_count, 1)


if __name__ == "__main__":
    unittest.main()
