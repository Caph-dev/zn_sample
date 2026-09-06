from __future__ import annotations

import unittest
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.creator_detail import EXTRACT_DETAIL_JS, extract_creator_detail, fetch_detail_for_row


class CreatorDetailReadTests(unittest.TestCase):
    def test_extractor_preserves_explicit_page_data_failure(self) -> None:
        detail = {
            "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/creator/detail?cid=creator-test",
            "detail_read_status": "page-data-unavailable",
            "detail_error_type": "page-data-unavailable",
            "detail_error_message": "数据加载失败，请稍后刷新",
            "video_gpm": "",
            "live_gpm": "",
        }
        with patch("lib.creator_detail.zclaw_exec", return_value=detail) as execute_script:
            result = extract_creator_detail("store-test")

        self.assertEqual(result["detail_read_status"], "page-data-unavailable")
        self.assertEqual(result["detail_error_type"], "page-data-unavailable")
        execute_script.assert_called_once()

    def test_page_data_failure_is_not_returned_as_empty_success(self) -> None:
        detail = {
            "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/creator/detail?cid=creator-test",
            "detail_read_status": "page-data-unavailable",
            "detail_error_type": "page-data-unavailable",
            "detail_error_message": "数据加载失败，请稍后刷新",
            "video_gpm": "",
            "live_gpm": "",
        }
        with (
            patch("lib.creator_detail.open_creator_detail_by_url", return_value={"ok": True}),
            patch("lib.creator_detail.extract_creator_detail", return_value=detail),
            patch("lib.creator_detail.go_back_to_list") as go_back,
        ):
            result = fetch_detail_for_row(
                "store-test",
                {"creator_id": "creator-test", "creator_name": "creator"},
                list_href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "page-data-unavailable")
        self.assertIn("资料读取失败", result["error"])
        go_back.assert_called_once()

    def test_extractor_script_contains_load_failure_contract(self) -> None:
        self.assertIn("detail_read_status", EXTRACT_DETAIL_JS)
        self.assertIn("数据加载失败，请稍后刷新", EXTRACT_DETAIL_JS)
        self.assertIn("server error", EXTRACT_DETAIL_JS.lower())


if __name__ == "__main__":
    unittest.main()
