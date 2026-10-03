"""Offline equivalence and safety checks for preview-only detail optimizations."""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.test_auto_approval_preview import (
    HERO_DATA,
    HERO_PRODUCT_ID,
    RULE_PAYLOAD,
    _pending_rows as make_legacy_pending_rows,
    _perf_events,
    _preview_arguments,
    auto_approval,
)
from lib.auto_approval_rules import (
    AutoApprovalRuleError,
    evaluate_custom_precheck,
    evaluate_custom_row,
    validate_custom_rule,
    verify_row_evidence,
)
from lib.sample_data_source import CreatorDetailResult

PASSING_DETAIL = {
    "video_gpm_n": 15, "avg_video_views_n": 800, "video_engagement_n": 3,
    "live_gpm_n": 20, "avg_live_views_n": 1500,
    "overall_gpm_n": 20, "aov_detail_n": 18, "est_post_rate_n": 95,
}
ALL_BASIC = {
    "followers": {"enabled": True, "min": 2000},
    "gmv": {"enabled": True, "min": 1500},
    "units": {"enabled": True, "min": 80},
    "female_pct": {"enabled": True, "min": 60},
    "categories": {"enabled": True, "values": ["Womenswear & Underwear"]},
    "gpm": {"enabled": True, "min": 10},
    "aov": {"enabled": True, "min": 10, "max": 25},
    "fulfillment": {"enabled": True, "min": 80},
}


def _pending_rows(count):
    rows = make_legacy_pending_rows(count)
    # The older video-only fixture uses display strings here. The list adapter
    # contract for enabled gender/category checks is structured list data.
    for row in rows:
        row["top_follower_gender"] = [{"key": "Female", "value": 0.7}]
        row["categories"] = ["Womenswear & Underwear"]
    return rows


def make_rule(*, basic=None, detail_enabled=True, logic="either", content_enabled=False):
    payload = deepcopy(RULE_PAYLOAD)
    payload["basic"] = deepcopy(ALL_BASIC if basic is None else basic)
    payload["video_live"]["enabled"] = detail_enabled
    if detail_enabled:
        payload["video_live"]["logic"] = logic
        payload["video_live"]["live"] = {
            "enabled": True, "gpm": 12, "avg_views": 1000,
        }
    else:
        payload["video_live"] = {"enabled": False}
    payload["content"] = {"enabled": content_enabled, "days": 7, "min_related": 4}
    return validate_custom_rule(payload, allowed_product_ids={HERO_PRODUCT_ID})


def evaluate(row, rule, *, precheck=False, **keywords):
    evaluator = evaluate_custom_precheck if precheck else evaluate_custom_row
    return evaluator(
        row, rule, hero_keys={HERO_PRODUCT_ID},
        allowed_product_ids={HERO_PRODUCT_ID}, **keywords,
    )


def run_preview(tmp_path, raw_rows, rule, *, collector=None, delay=0.25, iterations=1):
    arguments = _preview_arguments(tmp_path, rule.to_dict())
    arguments.detail_delay = delay
    with (
        patch.object(auto_approval, "_load_hero", return_value=deepcopy(HERO_DATA)),
        patch.object(auto_approval, "load_pending_rows", side_effect=lambda *args, **kwargs:
                     SimpleNamespace(rows=deepcopy(raw_rows), source_used="api", fallback_reason="")),
        patch.object(auto_approval, "load_creator_detail", side_effect=collector or (
            lambda *args, **kwargs: CreatorDetailResult(
                {"ok": True, "detail": deepcopy(PASSING_DETAIL)}, "api",
            )
        )) as detail_read,
        patch.object(auto_approval.time, "sleep") as sleep,
        patch.object(auto_approval, "review_creator_rows", side_effect=lambda rows: [
            {**row, "content_review_status": "passed", "content_review_complete": True,
             "content_review_related_count": 4} for row in rows
        ]) as content_review,
        patch.object(auto_approval, "approve_application_api") as approve,
        patch.object(auto_approval, "create_creator_relation_record") as feishu_write,
    ):
        for iteration in range(iterations):
            arguments.preview_id = f"prefilter-{iteration}"
            assert auto_approval._run_preview(arguments) == 0
        envelope = json.loads(arguments.out.read_text(encoding="utf-8"))
        approve.assert_not_called()
        feishu_write.assert_not_called()
        return envelope, detail_read, sleep, content_review


@pytest.mark.parametrize("field_name,threshold", [
    ("followers_n", 2000), ("gmv_n", 1500), ("units_n", 80), ("female_pct", 60),
])
@pytest.mark.parametrize("value_kind", ["missing", "below", "equal", "above"])
def test_list_only_thresholds_are_decisive(field_name, threshold, value_kind):
    row = auto_approval.enrich_row(_pending_rows(1)[0])
    row[field_name] = {
        "missing": None, "below": threshold - 1, "equal": threshold, "above": threshold + 1,
    }[value_kind]
    result = evaluate(row, make_rule(), precheck=True)
    assert result["should_fetch_detail"] == (value_kind == "above")
    if value_kind != "above":
        assert result["overall"] == "failed"


@pytest.mark.parametrize("categories", [[], ["Shoes"], ["Womenswear & Underwear"]])
def test_category_precheck_uses_existing_matching(categories):
    row = auto_approval.enrich_row(_pending_rows(1)[0])
    row["categories_n"] = categories
    assert evaluate(row, make_rule(), precheck=True)["should_fetch_detail"] == (
        categories == ["Womenswear & Underwear"]
    )


@pytest.mark.parametrize("overrides,code", [
    ({"apply_id": ""}, "missing-apply-id"),
    ({"creator_id": ""}, "missing-creator-id"),
    ({"product_id": ""}, "missing-product-id"),
    ({"product_id": "other"}, "not-active-product"),
    ({"can_be_approved": False}, "cannot-approve"),
])
def test_safety_blocks_stop_detail_without_changing_approval_state(overrides, code):
    row = {**auto_approval.enrich_row(_pending_rows(1)[0]), **overrides}
    result = evaluate(row, make_rule(), precheck=True)
    assert not result["should_fetch_detail"]
    assert code in {block["code"] for block in result["safety_blocks"]}
    assert result["blocked"]
    assert row.get("can_be_approved") == overrides.get("can_be_approved", True)


def test_no_hero_table_and_unknown_approval_state():
    row = auto_approval.enrich_row(_pending_rows(1)[0])
    row["can_be_approved"] = None
    rule = make_rule()
    assert evaluate(row, rule, precheck=True)["should_fetch_detail"]
    result = evaluate_custom_precheck(row, rule, hero_keys=set(), allowed_product_ids={HERO_PRODUCT_ID})
    assert not result["should_fetch_detail"]
    assert result["safety_blocks"][0]["code"] == "not-hero"


@pytest.mark.parametrize("description,status", [("6pcs,L", "failed"), (None, "needs_review"),
                                               ("-", "needs_review"), ("3PCS,M", "needs_review")])
def test_sku_and_deferred_evidence_remain_honest(description, status):
    row = auto_approval.enrich_row(_pending_rows(1)[0])
    row["sku_desc"] = description
    if description == "3PCS,M":
        row["can_be_approved"] = False
    rule = make_rule()
    result = evaluate(row, rule, defer_detail=True)
    assert result["overall"] == status
    for check in result["checks"]:
        if check["key"] in {"gpm", "aov", "fulfillment", "video_live"} or check["source"] == "detail-uncollected":
            assert check["status"] == "needs_review"
            assert check["value"] is None
            assert "未采集" in check["detail"]
    assert "detail_error" not in row and "detail_checked" not in row
    row["followers_n"] = 2000
    assert evaluate(row, rule, defer_detail=True)["overall"] == "failed"


@pytest.mark.parametrize("metric,field_name", [
    ("gpm", "gpm_proxy_n"), ("aov", "aov_n"), ("fulfillment", "fulfillment_n"),
])
@pytest.mark.parametrize("list_value", [None, 0])
def test_detail_overrides_get_a_chance_but_disabled_detail_uses_list(metric, field_name, list_value):
    row = auto_approval.enrich_row(_pending_rows(1)[0])
    row[field_name] = list_value
    basic = {metric: ALL_BASIC[metric]}
    detail_rule = make_rule(basic=basic)
    assert evaluate(row, detail_rule, precheck=True)["should_fetch_detail"]
    auto_approval._overlay_detail_fields(row, PASSING_DETAIL)
    assert evaluate(row, detail_rule)["custom_eligible"]
    list_row = auto_approval.enrich_row(_pending_rows(1)[0])
    list_row[field_name] = list_value
    list_rule = make_rule(basic=basic, detail_enabled=False)
    result = evaluate(list_row, list_rule, precheck=True)
    assert not result["should_fetch_detail"]
    assert result["overall"] == "failed"
    assert result["checks"] == evaluate(list_row, list_rule)["checks"]


def test_deferral_cannot_be_authorized_by_serialized_fields():
    rule = make_rule()
    row = auto_approval.enrich_row(_pending_rows(1)[0])
    row.update(detail_skip_reason="cannot-approve", defer_detail=True, custom_eligible=True)
    with pytest.raises(AutoApprovalRuleError, match="不能跳过详情"):
        evaluate(row, rule, defer_detail=True)
    complete_row = deepcopy(row)
    auto_approval._overlay_detail_fields(complete_row, PASSING_DETAIL)
    complete_evaluation = evaluate(complete_row, rule)
    for candidate in (row, complete_row):
        candidate.update(custom_eligible=True, custom_overall="passed", custom_checks=complete_evaluation["checks"])
    assert not verify_row_evidence(row, rule, hero_keys={HERO_PRODUCT_ID}, allowed_product_ids={HERO_PRODUCT_ID})[0]
    assert verify_row_evidence(complete_row, rule, hero_keys={HERO_PRODUCT_ID}, allowed_product_ids={HERO_PRODUCT_ID})[0]
    assert evaluate(row, rule)["overall"] == "failed"
    list_rule = make_rule(basic={"followers": ALL_BASIC["followers"]}, detail_enabled=False)
    checks_by_key = {check["key"]: check for check in evaluate(row, list_rule)["checks"]}
    assert checks_by_key["video_live"]["status"] == "not_checked"
    assert checks_by_key["gpm"]["status"] == "not_checked"


def performance_rows():
    rows = _pending_rows(8)
    rows[0]["follower_num"] = "2000"
    rows[1]["gmv"] = "$1500"
    rows[2]["sku_desc"] = "6PCS,L"
    rows[3]["sku_desc"] = None
    rows[4]["can_be_approved"] = False
    rows[6].update(creator_id=rows[5]["creator_id"], creator_name=rows[5]["creator_name"], sku_desc="3PCS,XL")
    rows[7].update(gmv="$5000", content_video_views="1000000", fulfillment_rate="10%")
    return rows


def test_eight_selected_rows_need_only_two_real_collections(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    raw_rows = performance_rows()
    envelope, detail_read, sleep, content_review = run_preview(tmp_path, raw_rows, make_rule())
    assert detail_read.call_count == 2
    assert sleep.call_count == 2
    assert content_review.call_count == 0
    assert envelope["integrity_complete"]
    assert [row["apply_id"] for row in envelope["rows"]] == [row["apply_id"] for row in raw_rows]
    total = _perf_events(caplog)[-1]["counts"]
    assert total["detail_prefilter_skipped"] == 5
    assert total["detail_target_rows"] == 3
    assert total["detail_cache_hit"] == 1
    assert total["detail_collections"] == total["detail_delay_calls"] == 2
    assert total["detail_circuit_skipped"] == 0
    assert all("detail_error" not in row for row in envelope["rows"])
    assert not any(row.get("detail_checked") for row in envelope["rows"][:5])


@pytest.mark.parametrize("detail_enabled,logic,content_enabled", [
    (True, "either", False), (True, "both", False), (True, "either", True),
    (False, "either", False), (False, "either", True),
])
def test_candidates_and_passing_checks_equal_unoptimized_snapshot(tmp_path, detail_enabled, logic, content_enabled):
    raw_rows = performance_rows() + _pending_rows(4)
    for index, row in enumerate(raw_rows[8:], 8):
        row.update(apply_id=f"apply-{index}", creator_id=f"creator-{index}", creator_name=f"creator_{index}")
    raw_rows[8]["creator_name"] = "missing-metrics"
    raw_rows[9]["creator_name"] = "live-only"
    raw_rows[10]["item_sold"] = "80"
    raw_rows[11]["product_id"] = "unselected"
    rule = make_rule(detail_enabled=detail_enabled, logic=logic, content_enabled=content_enabled)

    def collect_detail(store_id, row, **keywords):
        metrics = deepcopy(PASSING_DETAIL)
        if row["creator_name"] == "missing-metrics":
            metrics.update(video_gpm_n=None, live_gpm_n=None)
        if row["creator_name"] == "live-only":
            metrics["video_gpm_n"] = 0
        return CreatorDetailResult({"ok": True, "detail": metrics}, "api")

    reference = {}
    for raw in raw_rows:
        row = auto_approval.enrich_row(raw)
        if detail_enabled and row["product_id"] == HERO_PRODUCT_ID:
            auto_approval._overlay_detail_fields(row, collect_detail("store-test", row).result["detail"])
        reference[row["apply_id"]] = evaluate(row, rule)
    envelope, _, _, content_review = run_preview(tmp_path, raw_rows, rule, collector=collect_detail)
    expected_ids = {apply_id for apply_id, result in reference.items() if result["custom_eligible"]}
    assert {row["apply_id"] for row in envelope["rows"] if row["custom_eligible"]} == expected_ids
    for row in envelope["rows"]:
        if row["custom_eligible"]:
            assert row["custom_checks"] == reference[row["apply_id"]]["checks"]
    if content_enabled:
        reviewed = content_review.call_args.args[0]
        assert {row["apply_id"] for row in reviewed} == expected_ids
        assert all(row["custom_overall"] == "passed" and not row["blocked"] for row in reviewed)
    assert envelope["integrity_complete"]


@pytest.mark.parametrize("identity_kind,expected_calls", [
    ("same", 1), ("conflict", 3), ("missing", 3), ("same-name-different-id", 3),
])
def test_reuse_requires_consistent_nonempty_name_and_exact_id(tmp_path, identity_kind, expected_calls):
    raw_rows = _pending_rows(3)
    for row in raw_rows:
        row.update(creator_id=" 7391234567890123456 ", creator_name=" creator_same ")
    if identity_kind == "conflict":
        raw_rows[-1]["creator_name"] = "other_name"
    elif identity_kind == "missing":
        raw_rows[-1]["creator_name"] = " "
    elif identity_kind == "same-name-different-id":
        for index, row in enumerate(raw_rows):
            row["creator_id"] = str(7391234567890123456 + index)
    envelope, detail_read, sleep, _ = run_preview(tmp_path, raw_rows, make_rule(), iterations=2)
    assert detail_read.call_count == sleep.call_count == expected_calls * 2
    assert envelope["stats"]["eligible"] == 3
    assert detail_read.call_args.args[1]["creator_id"] == str(raw_rows[-1]["creator_id"]).strip()


@pytest.mark.parametrize("source_used,successful,expected_calls", [
    ("api", True, 1), ("dom-fallback", True, 3), ("dom", True, 3),
    ("api", False, 3), ("dom-fallback", False, 3),
])
def test_only_successful_api_is_cached_even_when_metrics_are_missing(tmp_path, source_used, successful, expected_calls):
    raw_rows = _pending_rows(3)
    for row in raw_rows:
        row.update(creator_id="same-id", creator_name="same_name")
    result = CreatorDetailResult({"ok": successful, "detail": {}, "error": "" if successful else "timeout"}, source_used)
    envelope, detail_read, sleep, _ = run_preview(tmp_path, raw_rows, make_rule(), collector=lambda *args, **kwargs: result)
    assert detail_read.call_count == expected_calls
    assert sleep.call_count == (expected_calls if successful else 0)
    assert envelope["integrity_complete"] == successful
    assert envelope["stats"]["eligible"] == 0
    if successful:
        assert all(row["custom_overall"] == "failed" for row in envelope["rows"])


def test_application_fields_and_cached_objects_are_isolated(tmp_path):
    raw_rows = _pending_rows(5)
    for row in raw_rows:
        row.update(creator_id="same-id", creator_name="same_name")
    raw_rows[1]["sku_desc"] = "3PCS,XL"
    raw_rows[2]["sku_desc"] = "6PCS,L"
    raw_rows[3]["sku_desc"] = None
    raw_rows[4]["can_be_approved"] = False
    original_detail = {**PASSING_DETAIL, "creator_type": ["mutable-marker"],
                       "apply_id": "wrong", "product_id": "wrong", "sku_desc": "6PCS",
                       "can_be_approved": False, "custom_checks": []}
    real_overlay = auto_approval._overlay_detail_fields

    def mutate_after_overlay(row, detail):
        real_overlay(row, detail)
        if row["apply_id"] == "apply-0":
            row["creator_type"].append("first-row-only")

    with patch.object(auto_approval, "_overlay_detail_fields", side_effect=mutate_after_overlay):
        envelope, detail_read, _, _ = run_preview(tmp_path, raw_rows, make_rule(), collector=lambda *args, **kwargs:
                                                CreatorDetailResult({"ok": True, "detail": original_detail}, "api"))
    assert detail_read.call_count == 1
    assert original_detail["creator_type"] == ["mutable-marker"]
    assert envelope["rows"][1]["creator_type"] == ["mutable-marker"]
    assert [row["sku_desc"] for row in envelope["rows"]] == [row["sku_desc"] for row in raw_rows]
    assert [row["custom_overall"] for row in envelope["rows"]] == ["passed", "passed", "failed", "needs_review", "needs_review"]
    assert envelope["rows"][-1]["blocked"]


def test_cache_and_prefilter_do_not_reset_or_bypass_circuit(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    raw_rows = _pending_rows(8)
    raw_rows[2].update(creator_id=raw_rows[0]["creator_id"], creator_name=raw_rows[0]["creator_name"])
    raw_rows[3]["sku_desc"] = "6PCS,L"
    raw_rows[6].update(creator_id=raw_rows[0]["creator_id"], creator_name=raw_rows[0]["creator_name"])
    calls = 0

    def collect_detail(*arguments, **keywords):
        nonlocal calls
        calls += 1
        return CreatorDetailResult(
            {"ok": True, "detail": PASSING_DETAIL} if calls == 1 else
            {"ok": False, "business_code": 100000, "error": "systemic"}, "api",
        )

    envelope, detail_read, sleep, _ = run_preview(tmp_path, raw_rows, make_rule(), collector=collect_detail)
    assert detail_read.call_count == 4
    assert sleep.call_count == 1
    counts = _perf_events(caplog)[-1]["counts"]
    assert counts["detail_cache_hit"] == counts["detail_prefilter_skipped"] == 1
    assert counts["detail_circuit_skipped"] == 2
    assert not envelope["integrity_complete"]
    assert "detail_error" not in envelope["rows"][3]
    assert envelope["rows"][6]["detail_error"] == "detail-api-systemic-failure"
    assert envelope["rows"][7]["detail_error"] == "detail-api-systemic-failure"
