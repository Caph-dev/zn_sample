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
    CreatorDetailResult,
    DetailFailureCircuit,
    compare_creator_details,
    load_creator_detail,
    load_pending_rows,
)
from lib.screening_perf import screening_run  # noqa: E402
from lib.operation_cancel import OperationCancelled  # noqa: E402


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


@pytest.mark.parametrize("api_result,systemic", [
    ({"ok": False, "business_code": 100000, "error": ""}, True),
    ({"ok": False, "error": "business failure code=100000"}, True),
    ({"ok": False, "error": "Please remove the plugin and try again"}, True),
    ({"ok": False, "error": ""}, False),
    ({"ok": False, "error": "request timeout"}, False),
    ({"ok": False, "error_type": "profile-schema-error", "error": "schema"}, False),
    ({"ok": False, "error": "request 100000 timed out"}, False),
])
def test_auto_systemic_failure_never_calls_successful_dom(api_result, systemic, caplog):
    caplog.set_level(logging.INFO)
    with (
        patch("lib.sample_data_source.fetch_creator_detail_api", return_value=api_result),
        patch("lib.sample_data_source.fetch_detail_for_row", return_value={
            "ok": True, "detail": {"video_gpm_n": 15},
        }) as dom_fetch,
        screening_run("systemic-source-test") as recorder,
    ):
        result = load_creator_detail("test", {}, data_source="auto", list_href="", wait=0)
    assert dom_fetch.call_count == int(not systemic)
    assert result.source_used == ("api" if systemic else "dom-fallback")
    assert bool(result.result["ok"]) == (not systemic)
    if systemic:
        assert result.result == api_result
        assert recorder.counts["reason_systemic_api_error"] == 1
        assert "detail_dom_calls" not in recorder.counts


@pytest.mark.parametrize("data_source", ["dom", "shadow", "api"])
@pytest.mark.parametrize("dom_success", [True, False])
def test_explicit_sources_preserve_systemic_api_and_dom_authority(data_source, dom_success):
    with (
        patch("lib.sample_data_source.fetch_creator_detail_api", return_value={
            "ok": False, "error": "code=100000", "business_code": 100000,
        }) as api_fetch,
        patch("lib.sample_data_source.fetch_detail_for_row", return_value={
            "ok": dom_success, "detail": {"video_gpm_n": 15}, "error": "ordinary failure",
        }) as dom_fetch,
    ):
        result = load_creator_detail("test", {}, data_source=data_source, list_href="", wait=0)
    assert api_fetch.call_count == int(data_source != "dom")
    assert dom_fetch.call_count == int(data_source != "api")
    assert result.result["ok"] == (False if data_source == "api" else dom_success)
    circuit = DetailFailureCircuit()
    assert circuit.observe(result, data_source=data_source) == (not result.result["ok"])
    assert circuit.consecutive_failures == int(
        data_source == "api" or (data_source == "shadow" and not dom_success)
    )
    if data_source == "shadow":
        assert result.source_used == "dom-shadow"
        assert not result.shadow_report["ok"]


@pytest.mark.parametrize("data_source", ["auto", "api"])
@pytest.mark.parametrize("sequence,expected_counts", [
    (["systemic", "success", "systemic", "systemic", "systemic"], [1, 0, 1, 2, 3]),
    (["systemic", "ordinary", "systemic"], [1, 0, 1]),
    (["systemic", "exception", "systemic"], [1, 0, 1]),
    (["systemic", "skip", "systemic", "cache", "systemic"], [1, 1, 2, 2, 3]),
    (["systemic_exception", "systemic_exception", "systemic_exception"], [1, 2, 3]),
    (["inconsistent", "inconsistent", "inconsistent"], [1, 2, 3]),
])
def test_circuit_tracks_only_real_collections(data_source, sequence, expected_counts):
    circuit = DetailFailureCircuit()
    outcomes = {
        "systemic": CreatorDetailResult({"ok": False, "business_code": 100000}, "api"),
        "success": CreatorDetailResult({"ok": True, "detail": {}}, "api"),
        "ordinary": CreatorDetailResult({"ok": False, "error": "timeout"}, "api"),
        "exception": RuntimeError("schema failure"),
        "systemic_exception": RuntimeError("Please remove the plugin"),
        "inconsistent": CreatorDetailResult(
            {"ok": True, "detail": {}}, "dom-fallback", fallback_reason="code=100000",
        ),
        "skip": CreatorDetailResult({"ok": False, "business_code": 100000}, "api"),
        "cache": CreatorDetailResult({"ok": True, "detail": {}}, "api"),
    }
    for event, expected_count in zip(sequence, expected_counts):
        failed = circuit.observe(
            outcomes[event], data_source=data_source, collected=event not in ("skip", "cache"),
        )
        assert circuit.consecutive_failures == expected_count
        assert circuit.is_open == (expected_count == 3)
        assert failed == (event not in ("success", "skip", "cache"))
    assert not DetailFailureCircuit().is_open


def test_circuit_cancellation_propagates_without_mutation():
    circuit = DetailFailureCircuit(consecutive_failures=2)
    with pytest.raises(OperationCancelled):
        circuit.observe(OperationCancelled("cancelled"), data_source="auto")
    assert circuit.consecutive_failures == 2


def test_auto_does_not_attribute_dom_only_error_to_api():
    circuit = DetailFailureCircuit(consecutive_failures=2)
    assert circuit.observe(CreatorDetailResult(
        {"ok": False, "error": "code=100000"}, "dom-fallback", fallback_reason="API timeout",
    ), data_source="auto")
    assert circuit.consecutive_failures == 0


@pytest.mark.parametrize("data_source", ["dom", "shadow"])
def test_legacy_exception_keeps_existing_streak(data_source):
    circuit = DetailFailureCircuit(consecutive_failures=2)
    assert circuit.observe(RuntimeError("ordinary timeout"), data_source=data_source)
    assert circuit.consecutive_failures == 2


if __name__ == "__main__":
    unittest.main()
