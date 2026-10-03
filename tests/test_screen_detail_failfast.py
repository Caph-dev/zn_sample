"""Offline tests through the formal screening entry and its real detail source."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import screen_sample_requests as screen  # noqa: E402
from lib.operation_cancel import OperationCancelled  # noqa: E402
from lib.sample_data_source import PendingListResult  # noqa: E402
from lib.screening_perf import screening_run, screening_run_active  # noqa: E402

PRODUCT_ID = "1732414717062320994"


def make_pending_rows(count):
    return [{
        "apply_id": f"apply-{index}", "creator_id": f"creator-{index}",
        "creator_name": f"creator_{index}", "product_id": PRODUCT_ID,
        "sku_desc": "3PCS,M", "can_be_approved": True,
        "follower_num": "5000", "gmv": "$2,000", "item_sold": "100",
        "content_video_views": "100000", "fulfillment_rate": "90%",
        "top_follower_gender": [{"key": "Female", "value": 0.7}],
        "categories": [{"name": "Womenswear & Underwear"}],
    } for index in range(count)]


def perf_events(caplog):
    return [json.loads(record.getMessage().removeprefix("[筛查耗时]"))
            for record in caplog.records if record.getMessage().startswith("[筛查耗时]")]


@pytest.mark.parametrize("data_source", ["auto", "api"])
def test_formal_read_only_main_stops_at_three_and_exports_fail_closed(tmp_path, caplog, data_source):
    caplog.set_level(logging.INFO)
    arguments = [
        "screen_sample_requests.py", "--store-id", "store-test", "--data-source", data_source,
        "--with-detail", "--require-detail", "--detail-delay", "0.25",
        "--out", str(tmp_path / "screen"),
    ]

    def fetch_api(*arguments, **keywords):
        assert screening_run_active()
        return {"ok": False, "business_code": 100000, "error": ""}

    with (
        patch.object(sys, "argv", arguments),
        patch.object(screen, "configure_logging"),
        patch.object(screen, "default_scan_store", return_value={"store_id": None, "store_name": ""}),
        patch.object(screen, "resolve_store_id", return_value="store-test"),
        patch.object(screen, "ensure_store_exec_ready"),
        patch.object(screen, "load_hero_from_feishu", return_value={
            "hero_keys": {PRODUCT_ID}, "hero_product_ids": [PRODUCT_ID],
        }),
        patch.object(screen, "load_pending_rows", side_effect=lambda *args, **kwargs:
                     PendingListResult(make_pending_rows(10), "api")),
        patch("lib.sample_data_source.fetch_creator_detail_api", side_effect=fetch_api) as api_fetch,
        patch("lib.sample_data_source.fetch_detail_for_row", return_value={
            "ok": True, "detail": {"video_gpm_n": 15},
        }) as dom_fetch,
        patch.object(screen.time, "sleep") as delay,
        patch.object(screen, "review_creator_rows") as content_review,
        patch.object(screen, "write_reports", return_value={}) as export,
        patch.object(screen, "write_reconciliation_manifest", return_value=tmp_path / "manifest.json"),
        patch.object(screen, "approve_application_api") as approve,
        patch.object(screen, "click_approve_for_apply_id") as approve_dom,
        patch.object(screen, "create_creator_relation_record") as write_feishu,
        patch.object(screen, "_run_execute_pipeline") as execute,
        patch.object(screen, "_run_confirm_pipeline") as confirm,
    ):
        assert not screening_run_active()
        assert screen.main() == int(data_source == "api")
        assert not screening_run_active()
        assert api_fetch.call_count == 3
        dom_fetch.assert_not_called()
        # Only the first two real results may retain the existing delay.
        assert delay.call_count == 2
        for operation in (content_review, approve, approve_dom, write_feishu, execute, confirm):
            operation.assert_not_called()
        exported_rows = export.call_args.args[0]
    assert len(exported_rows) == 10
    assert all(not row["eligible"] and row["approve_forbidden"] for row in exported_rows)
    assert all(row["action"] == "export-only" for row in exported_rows)
    assert sum(row.get("detail_error") == "detail-api-systemic-failure"
               for row in exported_rows[3:]) == 7
    events = perf_events(caplog)
    summary = events[-1]
    assert summary["stage"] == "detail_enrichment"
    assert summary["event"] == "end"
    assert summary["counts"]["detail_api_calls"] == 3
    assert summary["counts"]["detail_api_failure"] == 3
    assert summary["counts"]["detail_dom_calls"] == 0
    assert summary["counts"]["detail_circuit_skipped"] == 7
    assert summary["counts"]["detail_collections"] == 3
    assert summary["counts"]["reason_systemic_api_error"] == 3
    assert {event["run_id"] for event in events} == {summary["run_id"]}
    assert len([record for record in caplog.records if "连续 3 行" in record.getMessage()]) == 1


def enrich_details(rows, data_source="auto"):
    return screen._enrich_screen_details(
        "store-test", rows, rows, data_source=data_source, list_href="",
        page_wait=0, detail_delay=0, shadow_reports=[], screened_rows=rows,
    )


@pytest.mark.parametrize("middle", ["success", "ordinary", "exception"])
def test_formal_loop_interrupts_consecutiveness_with_real_source(middle, caplog):
    caplog.set_level(logging.INFO)
    outcomes = iter(["systemic", middle, "systemic", "systemic", "systemic"])

    def fetch_api(*arguments, **keywords):
        outcome = next(outcomes)
        if outcome == "exception":
            raise RuntimeError("ordinary timeout")
        if outcome == "success":
            return {"ok": True, "detail": {"video_gpm_n": 15}}
        return {"ok": False, "error": "code=100000" if outcome == "systemic" else "schema"}

    rows = make_pending_rows(6)
    with (
        patch("lib.sample_data_source.fetch_creator_detail_api", side_effect=fetch_api) as api_fetch,
        patch("lib.sample_data_source.fetch_detail_for_row", return_value={
            "ok": False, "error": "ordinary DOM failure",
        }) as dom_fetch,
    ):
        assert enrich_details(rows) == 0
    assert not screening_run_active()
    assert api_fetch.call_count == 5
    assert dom_fetch.call_count == int(middle == "ordinary")
    assert rows[-1]["detail_error"] == "detail-api-systemic-failure"
    assert rows[1].get("detail_checked", False) == (middle == "success")


@pytest.mark.parametrize("data_source", ["dom", "shadow"])
@pytest.mark.parametrize("dom_success", [True, False])
def test_formal_loop_keeps_legacy_dom_shadow_results(data_source, dom_success):
    rows = make_pending_rows(5)
    with (
        patch("lib.sample_data_source.fetch_creator_detail_api", return_value={
            "ok": False, "error": "code=100000",
        }) as api_fetch,
        patch("lib.sample_data_source.fetch_detail_for_row", return_value={
            "ok": dom_success, "detail": {"video_gpm_n": 15}, "error": "ordinary failure",
        }) as dom_fetch,
    ):
        assert enrich_details(rows, data_source) == 0
    expected_calls = 3 if data_source == "shadow" and not dom_success else 5
    assert dom_fetch.call_count == expected_calls
    assert api_fetch.call_count == (expected_calls if data_source == "shadow" else 0)
    assert sum(bool(row.get("detail_checked")) for row in rows) == (5 if dom_success else 0)


def test_formal_cancellation_resets_context_and_propagates():
    with (
        patch("lib.sample_data_source.fetch_creator_detail_api",
              side_effect=OperationCancelled("cancelled")) as api_fetch,
        patch("lib.sample_data_source.fetch_detail_for_row") as dom_fetch,
        pytest.raises(OperationCancelled),
    ):
        enrich_details(make_pending_rows(2))
    assert not screening_run_active()
    api_fetch.assert_called_once()
    dom_fetch.assert_not_called()


def test_formal_loop_reuses_existing_recorder_but_not_circuit():
    with (
        patch("lib.sample_data_source.fetch_creator_detail_api", return_value={
            "ok": False, "error": "code=100000",
        }) as api_fetch,
        patch("lib.sample_data_source.fetch_detail_for_row") as dom_fetch,
        screening_run("outer-task") as recorder,
    ):
        for iteration in range(2):
            enrich_details(make_pending_rows(10))
            assert screening_run_active()
        assert recorder.counts["detail_api_calls"] == 6
        assert recorder.counts["detail_circuit_skipped"] == 14
    assert not screening_run_active()
    assert api_fetch.call_count == 6
    dom_fetch.assert_not_called()
