"""自动审批规则：schema、三态评估、摘要与证据信封的单元测试。

不触发任何网络/写操作；评估只依赖内存构造的行数据。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.auto_approval_rules import (  # noqa: E402
    CONTENT_ALLOWED_DAYS,
    CONTENT_ALLOWED_MIN_RELATED,
    CONTENT_DISABLED_REASON,
    PREVIEW_FRESHNESS_SECONDS,
    AutoApprovalRuleError,
    evaluate_custom_row,
    rule_hash,
    rule_summary_lines,
    validate_custom_rule,
    verify_row_evidence,
)

ALLOWED = {"1732414717062320994", "999999"}


def rule_payload(**overrides) -> dict:
    payload = {
        "schema_version": 1,
        "mode": "custom",
        "product_ids": ["1732414717062320994"],
        "basic": {
            "fulfillment": {"enabled": True, "min": 85},
        },
        "video_live": {"enabled": False},
        "content": {"enabled": False},
    }
    payload.update(overrides)
    return payload


def base_row(**overrides) -> dict:
    row = {
        "apply_id": "apply-1",
        "creator_id": "creator-1",
        "creator_name": "creator_test",
        "product_id": "1732414717062320994",
        "fulfillment_n": 86.0,
        "est_post_rate_n": None,
        "can_be_approved": True,
    }
    row.update(overrides)
    return row


class RuleValidationTests(unittest.TestCase):
    def test_defaults_and_hash_are_stable(self) -> None:
        rule = validate_custom_rule(rule_payload(), allowed_product_ids=ALLOWED)
        again = validate_custom_rule(rule_payload(), allowed_product_ids=ALLOWED)
        self.assertEqual(rule_hash(rule), rule_hash(again))

    def test_empty_check_set_rejected(self) -> None:
        payload = {
            "schema_version": 1,
            "mode": "custom",
            "product_ids": ["1732414717062320994"],
            "basic": {},
            "video_live": {"enabled": False},
            "content": {"enabled": False},
        }
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        self.assertEqual(context.exception.code, "empty-check-set")

    def test_unknown_field_rejected(self) -> None:
        payload = rule_payload()
        payload["evil"] = True
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        self.assertEqual(context.exception.code, "unknown-field")

    def test_product_not_allowed_rejected(self) -> None:
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(
                rule_payload(product_ids=["424242"]),
                allowed_product_ids=ALLOWED,
            )
        self.assertEqual(context.exception.code, "product-not-allowed")

    def test_non_finite_number_rejected(self) -> None:
        with self.assertRaises(AutoApprovalRuleError):
            validate_custom_rule(
                rule_payload(basic={"fulfillment": {"enabled": True, "min": "85"}}),
                allowed_product_ids=ALLOWED,
            )

    def test_enabled_condition_requires_value(self) -> None:
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(
                rule_payload(basic={"fulfillment": {"enabled": True}}),
                allowed_product_ids=ALLOWED,
            )
        self.assertEqual(context.exception.code, "missing-value")

    def test_value_out_of_range_rejected(self) -> None:
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(
                rule_payload(basic={"fulfillment": {"enabled": True, "min": 250}}),
                allowed_product_ids=ALLOWED,
            )
        self.assertEqual(context.exception.code, "value-out-of-range")

    def test_aov_min_max_order(self) -> None:
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(
                rule_payload(basic={"aov": {"enabled": True, "min": 30, "max": 10}}),
                allowed_product_ids=ALLOWED,
            )
        self.assertEqual(context.exception.code, "invalid-range")

    def test_integer_fields(self) -> None:
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(
                rule_payload(
                    basic={"followers": {"enabled": True, "min": 2000.5}},
                ),
                allowed_product_ids=ALLOWED,
            )
        self.assertEqual(context.exception.code, "invalid-integer")

    def test_video_live_group_requires_side(self) -> None:
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(
                rule_payload(
                    video_live={"enabled": True, "logic": "either"},
                ),
                allowed_product_ids=ALLOWED,
            )
        self.assertEqual(context.exception.code, "missing-value")

    def test_both_logic_requires_both_sides(self) -> None:
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(
                rule_payload(
                    video_live={
                        "enabled": True,
                        "logic": "both",
                        "video": {"enabled": True, "gpm": 10, "avg_views": 300, "engagement": 2},
                    },
                ),
                allowed_product_ids=ALLOWED,
            )
        self.assertEqual(context.exception.code, "invalid-logic")

    def test_content_days_and_min_related_are_fixed(self) -> None:
        for field, value in (("days", 3), ("min_related", 6)):
            content = {"enabled": True, "days": 7, "min_related": 4, "require_display": True}
            content[field] = value
            with self.assertRaises(AutoApprovalRuleError) as context:
                validate_custom_rule(
                    rule_payload(content=content),
                    allowed_product_ids=ALLOWED,
                )
            self.assertEqual(context.exception.code, "value-out-of-range")

    def test_content_allowed_values_match_review_library(self) -> None:
        self.assertEqual(CONTENT_ALLOWED_DAYS, (7,))
        self.assertEqual(CONTENT_ALLOWED_MIN_RELATED, (4,))

    def test_to_dict_round_trip_revalidates(self) -> None:
        """快照必须能被自己校验：预览信封/规则文件都依赖 to_dict() 回读。"""
        rule = validate_custom_rule(rule_payload(), allowed_product_ids=ALLOWED)
        again = validate_custom_rule(rule.to_dict(), allowed_product_ids=ALLOWED)
        self.assertEqual(rule_hash(rule), rule_hash(again))

    def test_to_dict_round_trip_with_all_groups(self) -> None:
        payload = rule_payload(
            basic={
                "followers": {"enabled": True, "min": 2000},
                "aov": {"enabled": True, "min": 10, "max": 25},
                "categories": {
                    "enabled": True,
                    "values": ["Beauty & Personal Care"],
                },
            },
            video_live={
                "enabled": True,
                "logic": "either",
                "video": {
                    "enabled": True,
                    "gpm": 10,
                    "avg_views": 300,
                    "engagement": 2,
                },
                "live": {"enabled": False},
            },
            content={
                "enabled": True,
                "days": 7,
                "min_related": 4,
                "require_display": True,
            },
        )
        rule = validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        again = validate_custom_rule(rule.to_dict(), allowed_product_ids=ALLOWED)
        self.assertEqual(rule_hash(rule), rule_hash(again))

    def test_explicit_nulls_in_disabled_sides_are_accepted(self) -> None:
        """历史快照可能把禁用侧写成 null；必须仍可校验通过。"""
        payload = rule_payload(
            video_live={
                "enabled": False,
                "logic": "either",
                "video": {
                    "enabled": False,
                    "gpm": None,
                    "avg_views": None,
                    "engagement": None,
                },
                "live": {
                    "enabled": False,
                    "gpm": None,
                    "avg_views": None,
                    "engagement": None,
                },
            }
        )
        rule = validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        self.assertFalse(rule.video_live.enabled)

    def test_enabled_side_with_null_metric_reports_missing_value(self) -> None:
        payload = rule_payload(
            basic={},
            video_live={
                "enabled": True,
                "logic": "either",
                "video": {
                    "enabled": True,
                    "gpm": None,
                    "avg_views": 300,
                    "engagement": 2,
                },
            },
        )
        with self.assertRaises(AutoApprovalRuleError) as context:
            validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        self.assertEqual(context.exception.code, "missing-value")


class RuleEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = validate_custom_rule(rule_payload(), allowed_product_ids=ALLOWED)

    def test_boundary_85_fails_86_passes(self) -> None:
        at_threshold = evaluate_custom_row(
            base_row(fulfillment_n=85.0),
            self.rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        self.assertEqual(at_threshold["overall"], "failed")
        self.assertFalse(at_threshold["custom_eligible"])
        above = evaluate_custom_row(
            base_row(fulfillment_n=86.0),
            self.rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        self.assertEqual(above["overall"], "passed")
        self.assertTrue(above["custom_eligible"])

    def test_missing_metric_fails(self) -> None:
        """启用指标缺失按不通过处理，不默认放过。"""
        result = evaluate_custom_row(
            base_row(fulfillment_n=None),
            self.rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        self.assertEqual(result["overall"], "failed")
        self.assertFalse(result["custom_eligible"])

    def test_detail_collection_failure_stays_needs_review(self) -> None:
        """详情采集失败属于「没采到」，不是「平台没有」；批次本身禁止执行。"""
        result = evaluate_custom_row(
            base_row(fulfillment_n=None, detail_error="detail-api-systemic-failure"),
            self.rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        self.assertEqual(result["overall"], "needs_review")
        self.assertFalse(result["custom_eligible"])

    def test_categories_missing_fails(self) -> None:
        payload = rule_payload(
            basic={"categories": {"enabled": True, "values": ["Beauty & Personal Care"]}}
        )
        rule = validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        result = evaluate_custom_row(
            base_row(categories_n=[]),
            rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        self.assertEqual(result["overall"], "failed")

    def test_disabled_metric_is_not_checked(self) -> None:
        result = evaluate_custom_row(
            base_row(),
            self.rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        gmv_check = next(
            check for check in result["checks"] if check["key"] == "gmv"
        )
        self.assertEqual(gmv_check["status"], "not_checked")

    def test_hero_and_product_blocks(self) -> None:
        result = evaluate_custom_row(
            base_row(product_id="111111"),
            self.rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        self.assertTrue(result["blocked"])
        self.assertFalse(result["custom_eligible"])
        codes = {block["code"] for block in result["safety_blocks"]}
        self.assertIn("not-active-product", codes)

    def test_fulfillment_prefers_est_post_rate(self) -> None:
        result = evaluate_custom_row(
            base_row(fulfillment_n=10.0, est_post_rate_n=90.0),
            self.rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        check = next(
            item for item in result["checks"] if item["key"] == "fulfillment"
        )
        self.assertEqual(check["source"], "detail-est-post-rate")
        self.assertEqual(check["status"], "passed")

    def test_aov_prefers_detail_value(self) -> None:
        payload = rule_payload(basic={"aov": {"enabled": True, "min": 10, "max": 25}})
        rule = validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        result = evaluate_custom_row(
            base_row(aov_n=90.0, aov_detail_n=18.75),
            rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        check = next(item for item in result["checks"] if item["key"] == "aov")
        self.assertEqual(check["source"], "detail")
        self.assertEqual(check["value"], 18.75)
        self.assertEqual(check["status"], "passed")

    def test_aov_falls_back_to_derived_value(self) -> None:
        payload = rule_payload(basic={"aov": {"enabled": True, "min": 10, "max": 25}})
        rule = validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        result = evaluate_custom_row(
            base_row(aov_n=90.0, aov_detail_n=None),
            rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        check = next(item for item in result["checks"] if item["key"] == "aov")
        self.assertEqual(check["source"], "derived")
        self.assertEqual(check["value"], 90.0)
        self.assertEqual(check["status"], "failed")

    def test_video_live_either_and_both(self) -> None:
        payload = rule_payload(
            basic={},
            video_live={
                "enabled": True,
                "logic": "either",
                "video": {"enabled": True, "gpm": 10, "avg_views": 300, "engagement": 2},
                "live": {"enabled": True, "gpm": 12, "avg_views": 1000},
            },
            content={"enabled": False},
        )
        rule = validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        row = base_row(
            video_gpm_n=15.0,
            avg_video_views_n=500.0,
            video_engagement_n=5.0,
            live_gpm_n=1.0,
            avg_live_views_n=10.0,
        )
        result = evaluate_custom_row(
            row, rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        self.assertEqual(result["overall"], "passed")
        both_payload = payload
        both_payload["video_live"]["logic"] = "both"
        both_rule = validate_custom_rule(both_payload, allowed_product_ids=ALLOWED)
        result = evaluate_custom_row(
            row, both_rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        self.assertEqual(result["overall"], "failed")

    def test_missing_video_metrics_fail(self) -> None:
        payload = rule_payload(
            basic={},
            video_live={
                "enabled": True,
                "logic": "either",
                "video": {"enabled": True, "gpm": 10, "avg_views": 300, "engagement": 2},
                "live": {"enabled": False},
            },
            content={"enabled": False},
        )
        rule = validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        result = evaluate_custom_row(
            base_row(), rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        self.assertEqual(result["overall"], "failed")

    def test_either_group_with_single_enabled_side_fails(self) -> None:
        """任一侧达标：唯一启用侧未达标（含缺失）时整组不通过，而不是待复核。"""
        payload = rule_payload(
            basic={},
            video_live={
                "enabled": True,
                "logic": "either",
                "video": {"enabled": True, "gpm": 10, "avg_views": 300, "engagement": 2},
                "live": {"enabled": False},
            },
            content={"enabled": False},
        )
        rule = validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        result = evaluate_custom_row(
            base_row(
                video_gpm_n=5.0,
                avg_video_views_n=500.0,
                video_engagement_n=5.0,
            ),
            rule,
            hero_keys=ALLOWED,
            allowed_product_ids=ALLOWED,
        )
        group_check = next(
            check for check in result["checks"] if check["key"] == "video_live"
        )
        self.assertEqual(group_check["status"], "failed")
        self.assertEqual(result["overall"], "failed")


class SummaryTests(unittest.TestCase):
    def test_summary_mentions_custom_and_not_checked(self) -> None:
        rule = validate_custom_rule(rule_payload(), allowed_product_ids=ALLOWED)
        lines = rule_summary_lines(
            rule, product_display={"1732414717062320994": "B005"}
        )
        joined = "\n".join(lines)
        self.assertIn("自定义", joined)
        self.assertIn("not_checked", joined)
        self.assertIn("未执行", joined)
        self.assertIn("B005", joined)


class EvidenceVerificationTests(unittest.TestCase):
    def test_content_disabled_evidence(self) -> None:
        rule = validate_custom_rule(rule_payload(), allowed_product_ids=ALLOWED)
        row = base_row()
        evaluation = evaluate_custom_row(
            row, rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        row.update(
            {
                "custom_overall": evaluation["overall"],
                "custom_checks": evaluation["checks"],
                "custom_eligible": True,
                "content_review_status": "not_run",
                "content_review_reason": CONTENT_DISABLED_REASON,
            }
        )
        valid, reason = verify_row_evidence(
            row, rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        self.assertTrue(valid, reason)

    def test_content_disabled_with_forged_pass_rejected(self) -> None:
        rule = validate_custom_rule(rule_payload(), allowed_product_ids=ALLOWED)
        row = base_row()
        evaluation = evaluate_custom_row(
            row, rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        row.update(
            {
                "custom_overall": evaluation["overall"],
                "custom_checks": evaluation["checks"],
                "custom_eligible": True,
                "content_review_status": "passed",
                "content_review_reason": "forged",
            }
        )
        valid, _reason = verify_row_evidence(
            row, rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        self.assertFalse(valid)

    def test_content_enabled_requires_real_proof(self) -> None:
        payload = rule_payload(content={"enabled": True, "days": 7, "min_related": 4, "require_display": True})
        rule = validate_custom_rule(payload, allowed_product_ids=ALLOWED)
        row = base_row()
        evaluation = evaluate_custom_row(
            row, rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        row.update(
            {
                "custom_overall": evaluation["overall"],
                "custom_checks": evaluation["checks"],
                "custom_eligible": True,
                "content_review_status": "not_run",
                "content_review_reason": "sales_not_eligible",
            }
        )
        valid, _reason = verify_row_evidence(
            row, rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        self.assertFalse(valid)

    def test_evaluation_changed_rejected(self) -> None:
        rule = validate_custom_rule(rule_payload(), allowed_product_ids=ALLOWED)
        row = base_row(fulfillment_n=10.0)  # 低于阈值 85
        evaluation = evaluate_custom_row(
            row, rule, hero_keys=ALLOWED, allowed_product_ids=ALLOWED
        )
        self.assertFalse(evaluation["custom_eligible"])


class FreshnessConstantTests(unittest.TestCase):
    def test_freshness_is_24_hours(self) -> None:
        self.assertEqual(PREVIEW_FRESHNESS_SECONDS, 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
