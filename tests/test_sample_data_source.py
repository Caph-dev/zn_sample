from __future__ import annotations

import json
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.sample_data_source import (  # noqa: E402
    compare_creator_details,
    load_creator_detail,
    load_pending_rows,
)
from lib.screening_perf import screening_run  # noqa: E402


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
    def test_detail_auto_does_not_open_dom_when_gpm_is_missing(
        self,
        api_fetcher,
        dom_fetcher,
    ) -> None:
        api_fetcher.return_value = {
            "ok": True,
            "detail": {
                "video_gpm": "",
                "live_gpm": "",
                "video_gpm_n": None,
                "live_gpm_n": None,
            },
        }

        result = load_creator_detail(
            "store-test",
            {"apply_id": "apply-test-001", "creator_id": "creator-test-001"},
            data_source="auto",
            list_href="https://example.test/sample-request",
            wait=0,
        )

        self.assertEqual(result.source_used, "api")
        self.assertIsNone(result.result["detail"]["video_gpm_n"])
        dom_fetcher.assert_not_called()

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


@pytest.mark.parametrize("dom_failure", [False, True])
def test_observed_fallback_counts_actual_sources_without_secret(caplog, dom_failure):
    caplog.set_level(logging.INFO)
    sequence = []

    def api_read(*args, **kwargs):
        sequence.append("api")
        return {"ok": False, "error": "SIGNED_MEDIA_URL_SECRET", "error_type": "profile-business-error"}

    def dom_read(*args, **kwargs):
        sequence.append("dom")
        assert kwargs == {"list_href": "https://example.test/", "prefer_url": True, "wait": 3.5}
        if dom_failure:
            raise RuntimeError("Authorization=SECRET_SENTINEL")
        return {"ok": True, "detail": {"video_gpm_n": 12}}

    with (
        patch("lib.sample_data_source.fetch_creator_detail_api", side_effect=api_read),
        patch("lib.sample_data_source.fetch_detail_for_row", side_effect=dom_read),
        screening_run("detail-fallback") as recorder,
    ):
        if dom_failure:
            with pytest.raises(RuntimeError, match="SECRET_SENTINEL"):
                load_creator_detail("test", {}, data_source="auto", list_href="https://example.test/", wait=3.5)
        else:
            result = load_creator_detail("test", {}, data_source="auto", list_href="https://example.test/", wait=3.5)
            assert result.source_used == "dom-fallback"
            assert result.result["detail"]["video_gpm_n"] == 12
    assert sequence == ["api", "dom"]
    assert recorder.counts["detail_api_calls"] == recorder.counts["detail_api_failure"] == 1
    assert recorder.counts["detail_dom_calls"] == 1
    assert recorder.counts["detail_dom_failure" if dom_failure else "detail_dom_success"] == 1
    events = [json.loads(record.getMessage().removeprefix("[筛查耗时]")) for record in caplog.records
              if record.getMessage().startswith("[筛查耗时]")]
    assert "SECRET" not in json.dumps(events)
    assert events[-1]["counts"]["reason_api_fallback"] == 1
    assert events[-1]["status"] == ("error" if dom_failure else "success")
    assert len(events) == 4


if __name__ == "__main__":
    unittest.main()
