from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.sample_data_source import (  # noqa: E402
    compare_creator_details,
    load_creator_detail,
    load_pending_rows,
)


class PendingDataSourceTests(unittest.TestCase):
    @patch("lib.sample_data_source.scrape_pending_list")
    @patch("lib.sample_data_source.scrape_pending_list_api")
    def test_auto_falls_back_to_dom_for_read_failure(
        self,
        api_scraper,
        dom_scraper,
    ) -> None:
        api_scraper.side_effect = RuntimeError("synthetic API failure")
        dom_scraper.return_value = [{"apply_id": "apply-test-001"}]

        result = load_pending_rows(
            "store-test",
            data_source="auto",
            max_pages=1,
            max_rows=1,
            page_wait=0,
        )

        self.assertEqual(result.source_used, "dom-fallback")
        self.assertIn("synthetic API failure", result.fallback_reason)
        self.assertEqual(result.rows[0]["_data_source"], "dom-fallback")

    @patch("lib.sample_data_source.scrape_pending_list")
    @patch("lib.sample_data_source.scrape_pending_list_api")
    def test_shadow_uses_dom_as_authoritative_rows(
        self,
        api_scraper,
        dom_scraper,
    ) -> None:
        api_scraper.return_value = [
            {"apply_id": "apply-test-001", "gmv": "$2,400"}
        ]
        dom_scraper.return_value = [
            {"apply_id": "apply-test-001", "gmv": "$2,400"}
        ]

        result = load_pending_rows(
            "store-test",
            data_source="shadow",
            max_pages=1,
            max_rows=1,
            page_wait=0,
        )

        self.assertEqual(result.source_used, "dom-shadow")
        self.assertTrue(result.shadow_report["ok"])
        self.assertEqual(result.rows[0]["_data_source"], "dom-shadow")


class CreatorDetailDataSourceTests(unittest.TestCase):
    def test_detail_comparison_accepts_rounding_and_api_only_fields(self) -> None:
        report = compare_creator_details(
            {"apply_id": "apply-test-001"},
            {
                "video_gpm_n": 12.5,
                "live_gpm_n": 0.0,
                "avg_video_views_n": 450.0,
                "aov_detail_n": None,
            },
            {
                "video_gpm_n": 12.52,
                "live_gpm_n": 0.0,
                "avg_video_views_n": 450.0,
                "aov_detail_n": 18.75,
            },
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["comparison_status"], "matched-with-api-extra")
        self.assertEqual(report["api_only_fields"], ["aov_detail_n"])
        self.assertNotEqual(report["row"], "apply-test-001")

    def test_detail_comparison_accepts_exact_tolerance_boundary(self) -> None:
        report = compare_creator_details(
            {"apply_id": "apply-test-001"},
            {"aov_detail_n": 7.6},
            {"aov_detail_n": 7.65},
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["comparison_status"], "matched")
        self.assertEqual(report["mismatches"], [])

    def test_detail_comparison_marks_api_more_complete(self) -> None:
        report = compare_creator_details(
            {"apply_id": "apply-test-001"},
            {},
            {
                "video_gpm_n": 12.0,
                "live_gpm_n": 0.0,
            },
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["comparison_status"], "api-more-complete")
        self.assertEqual(
            report["api_only_fields"],
            ["video_gpm_n", "live_gpm_n"],
        )

    @patch("lib.sample_data_source.fetch_detail_for_row")
    @patch("lib.sample_data_source.fetch_creator_detail_api")
    def test_detail_auto_falls_back_to_dom_when_api_fails(
        self,
        api_fetcher,
        dom_fetcher,
    ) -> None:
        api_fetcher.return_value = {"ok": False, "error": "synthetic detail failure"}
        dom_fetcher.return_value = {
            "ok": True,
            "detail": {"video_gpm_n": 13.0},
        }

        result = load_creator_detail(
            "store-test",
            {"apply_id": "apply-test-001", "creator_id": "creator-test-001"},
            data_source="auto",
            list_href="https://example.test/sample-request",
            wait=0,
        )

        self.assertEqual(result.source_used, "dom-fallback")
        self.assertIn("synthetic detail failure", result.fallback_reason)
        self.assertEqual(
            result.result["detail"]["_detail_data_source"],
            "dom-fallback",
        )

    @patch("lib.sample_data_source.fetch_detail_for_row")
    @patch("lib.sample_data_source.fetch_creator_detail_api")
    def test_detail_shadow_keeps_dom_authoritative(
        self,
        api_fetcher,
        dom_fetcher,
    ) -> None:
        api_fetcher.return_value = {
            "ok": True,
            "detail": {"video_gpm_n": 13.0},
        }
        dom_fetcher.return_value = {
            "ok": True,
            "detail": {"video_gpm_n": 13.0},
        }

        result = load_creator_detail(
            "store-test",
            {"apply_id": "apply-test-001", "creator_id": "creator-test-001"},
            data_source="shadow",
            list_href="https://example.test/sample-request",
            wait=0,
        )

        self.assertEqual(result.source_used, "dom-shadow")
        self.assertTrue(result.shadow_report["ok"])
        self.assertEqual(
            result.result["detail"]["_detail_data_source"],
            "dom-shadow",
        )


if __name__ == "__main__":
    unittest.main()
