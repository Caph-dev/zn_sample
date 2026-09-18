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

    @patch("lib.sample_dom.zclaw_exec")
    def test_extract_page_raises_when_body_is_missing(self, execute_script) -> None:
        from lib.sample_dom import extract_page

        execute_script.return_value = {
            "ok": False,
            "reason": "no-document-body",
            "href": "https://seller.tiktokshopglobalselling.com/order",
            "rows": [],
        }
        with self.assertRaisesRegex(RuntimeError, "document.body"):
            extract_page("store-test")


class ShippedTabReadinessTests(unittest.TestCase):
    @patch("lib.shipped_dom.time.sleep")
    @patch("lib.shipped_dom.ensure_sample_request_context")
    @patch("lib.shipped_dom.zclaw_exec")
    def test_retries_when_inner_shipped_tab_is_still_loading(
        self,
        execute_script,
        ensure_context,
        sleep,
    ) -> None:
        from lib.shipped_dom import ensure_on_sample_page

        sample_href = (
            "https://affiliate.tiktokshopglobalselling.com/"
            "affiliate/sample/sample-request?shop_region=US&shop_id=shop-test"
        )
        execute_script.side_effect = [
            {
                "ok": False,
                "reason": "no-shipped-tab",
                "href": sample_href,
                "titles": ["免费样品", "买返样品"],
            },
            {
                "ok": True,
                "clicked": True,
                "href": sample_href,
                "text": "已发货\n55",
            },
        ]

        result = ensure_on_sample_page("store-test", page_wait=1.5, retries=2)

        self.assertTrue(result["ok"])
        self.assertTrue(result["clicked"])
        self.assertEqual(execute_script.call_count, 2)
        ensure_context.assert_called_once()
        sleep.assert_any_call(1.5)

    @patch("lib.shipped_dom.ensure_sample_request_context")
    @patch("lib.shipped_dom.zclaw_exec")
    def test_does_not_retry_when_not_on_sample_page(
        self,
        execute_script,
        ensure_context,
    ) -> None:
        from lib.shipped_dom import ensure_on_sample_page

        execute_script.return_value = {
            "ok": False,
            "reason": "not-on-sample-page",
            "href": "https://affiliate.tiktokshopglobalselling.com/platform/homepage",
        }

        with self.assertRaisesRegex(RuntimeError, "无法切到「已发货」tab"):
            ensure_on_sample_page("store-test", page_wait=0, retries=2)

        self.assertEqual(execute_script.call_count, 1)
        ensure_context.assert_called_once()


if __name__ == "__main__":
    unittest.main()
