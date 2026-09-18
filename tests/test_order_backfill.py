"""订单号补写：飞书扫描、平台对齐、写入与报告的纯逻辑测试。"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib import order_backfill  # noqa: E402

VALID_ORDER_NO = "123456789012345678"


def _feishu_record(record_id: str, created_ms: int, handle: str = "creator_a", product: str = "B005") -> dict:
    return {
        "record_id": record_id,
        "created_time": created_ms,
        "fields": {"红人ID": handle, "寄样产品": product, "合作状态": ["待发货"]},
    }


class CollectCandidatesTests(unittest.TestCase):
    def test_scans_the_fixed_rule_and_drops_rows_without_identity(self):
        records = [
            _feishu_record("rec-2", 2_000_000, handle="creator_b"),
            _feishu_record("rec-1", 1_000_000, handle="creator_a"),
            _feishu_record("rec-3", 3_000_000, handle=""),
        ]
        with patch.object(
            order_backfill, "search_records_missing_order_no", return_value=records
        ) as search:
            rows = order_backfill.collect_order_backfill_candidates("token", lookback_hours=72)

        self.assertEqual([row["record_id"] for row in rows], ["rec-1", "rec-2"])
        self.assertEqual(search.call_args.kwargs["person"], "王良希（技术）")
        since = search.call_args.kwargs["since"]
        expected = datetime.now(timezone.utc) - timedelta(hours=72)
        self.assertIsNotNone(since.tzinfo)
        self.assertLess(abs((since - expected).total_seconds()), 60)
        self.assertEqual(rows[0]["creator_handle"], "creator_a")
        self.assertEqual(rows[0]["sample_product"], "B005")

    def test_invalid_lookback_hours_still_scans_at_least_one_hour(self):
        with patch.object(
            order_backfill, "search_records_missing_order_no", return_value=[]
        ) as search:
            order_backfill.collect_order_backfill_candidates("token", lookback_hours=0)
        since = search.call_args.kwargs["since"]
        self.assertLess(abs((since - (datetime.now(timezone.utc) - timedelta(hours=1))).total_seconds()), 60)


class SelectPlatformOrderTests(unittest.TestCase):
    def setUp(self):
        self.product_id_to_sku = {"pid-1": "B005"}
        self.options = ["B005"]

    def _select(self, rows, *, handle="creator_a", product="B005"):
        return order_backfill.select_platform_order(
            rows,
            creator_handle=handle,
            sample_product=product,
            product_id_to_sku=self.product_id_to_sku,
            sample_product_options=self.options,
        )

    def test_matches_creator_and_product_across_tabs(self):
        rows = [
            {"creator_name": "creator_a", "product_id": "pid-1", "main_order_id": "0",
             "apply_id": "apply-pending", order_backfill.PLATFORM_TAB_FIELD: "待审核"},
            {"creator_name": "Creator_A", "product_id": "pid-1", "main_order_id": VALID_ORDER_NO,
             "apply_id": "apply-shipped", order_backfill.PLATFORM_TAB_FIELD: "已发货"},
        ]
        picked = self._select(rows)
        self.assertEqual(picked["status"], "matched")
        self.assertEqual(picked["order_no"], VALID_ORDER_NO)
        self.assertEqual(picked["apply_id"], "apply-shipped")
        self.assertEqual(picked["platform_tab"], "已发货")

    def test_other_products_of_the_same_creator_are_ignored(self):
        rows = [
            {"creator_name": "creator_a", "product_id": "other-pid", "main_order_id": VALID_ORDER_NO,
             order_backfill.PLATFORM_TAB_FIELD: "待审核"},
        ]
        picked = self._select(rows)
        self.assertEqual(picked["status"], "no-platform-order")
        self.assertEqual(picked["order_no"], "")
        self.assertIn("搜不到匹配", picked["detail"])

    def test_found_application_without_order_number_is_reported(self):
        rows = [
            {"creator_name": "creator_a", "product_id": "pid-1", "main_order_id": "0",
             order_backfill.PLATFORM_TAB_FIELD: "待审核"},
        ]
        picked = self._select(rows)
        self.assertEqual(picked["status"], "no-platform-order")
        self.assertEqual(picked["platform_tab"], "待审核")
        self.assertIn("还没有订单号", picked["detail"])

    def test_different_order_numbers_are_ambiguous(self):
        rows = [
            {"creator_name": "creator_a", "product_id": "pid-1", "main_order_id": VALID_ORDER_NO,
             order_backfill.PLATFORM_TAB_FIELD: "待发货"},
            {"creator_name": "creator_a", "product_id": "pid-1", "main_order_id": "876543210987654321",
             order_backfill.PLATFORM_TAB_FIELD: "已发货"},
        ]
        picked = self._select(rows)
        self.assertEqual(picked["status"], "ambiguous")
        self.assertEqual(picked["order_no"], "")


class PlanTests(unittest.TestCase):
    def test_plan_merges_candidate_and_lookup(self):
        candidates = [
            {"record_id": "rec-1", "creator_handle": "creator_a", "sample_product": "B005"},
            {"record_id": "rec-2", "creator_handle": "creator_b", "sample_product": "B005"},
        ]
        lookups = {
            "rec-1": {
                "status": "matched", "order_no": VALID_ORDER_NO, "apply_id": "apply-1",
                "platform_tab": "待发货", "detail": "后台待发货已给出订单号",
            },
        }
        plans = order_backfill.plan_order_backfill(candidates, lookups)
        self.assertEqual([plan["status"] for plan in plans], ["matched", "no-platform-order"])
        self.assertEqual(plans[0]["order_no"], VALID_ORDER_NO)
        self.assertEqual(plans[0]["creator_handle"], "creator_a")
        self.assertEqual(plans[1]["order_no"], "")

    def test_unknown_lookup_becomes_no_platform_order(self):
        plans = order_backfill.plan_order_backfill(
            [{"record_id": "rec-1", "creator_handle": "creator_a", "sample_product": "B005"}], {}
        )
        self.assertEqual(plans[0]["status"], "no-platform-order")


class ApplyTests(unittest.TestCase):
    def _plans(self, count: int = 1):
        return [
            {
                "record_id": f"rec-{index}", "creator_handle": f"creator_{index}",
                "sample_product": "B005", "status": "matched",
                "order_no": VALID_ORDER_NO, "apply_id": f"apply-{index}",
            }
            for index in range(count)
        ]

    def test_dry_run_never_writes(self):
        with patch.object(order_backfill, "update_record_order_no") as update:
            results = order_backfill.apply_order_backfill(
                self._plans(), access_token="token", write_feishu=False
            )
        update.assert_not_called()
        self.assertEqual(results[0]["status"], "planned")

    def test_write_outcomes_map_to_report_statuses(self):
        outcomes = [
            ("written", "written"),
            ("unchanged", "unchanged"),
            ("skipped-existing-different", "conflict"),
            ("write-uncertain", "write-uncertain"),
        ]
        with patch.object(
            order_backfill, "update_record_order_no",
            side_effect=[{"status": status} for status, _ in outcomes],
        ):
            results = order_backfill.apply_order_backfill(
                self._plans(4), access_token="token", write_feishu=True
            )
        self.assertEqual([row["status"] for row in results], [mapped for _, mapped in outcomes])

    def test_write_error_is_uncertain_and_never_raises(self):
        with patch.object(
            order_backfill, "update_record_order_no",
            side_effect=RuntimeError("network down"),
        ):
            results = order_backfill.apply_order_backfill(
                self._plans(), access_token="token", write_feishu=True
            )
        self.assertEqual(results[0]["status"], "write-uncertain")
        self.assertIn("network down", results[0]["detail"])

    def test_limit_stops_further_writes(self):
        with patch.object(
            order_backfill, "update_record_order_no",
            return_value={"status": "written"},
        ) as update:
            results = order_backfill.apply_order_backfill(
                self._plans(3), access_token="token", write_feishu=True, limit=2
            )
        self.assertEqual(update.call_count, 2)
        self.assertEqual([row["status"] for row in results], ["written", "written", "skipped-limit"])

    def test_non_matched_plans_are_reported_as_is(self):
        plans = [{"record_id": "rec-1", "creator_handle": "creator_a", "sample_product": "B005",
                  "status": "no-platform-order", "order_no": "", "detail": "无匹配"}]
        with patch.object(order_backfill, "update_record_order_no") as update:
            results = order_backfill.apply_order_backfill(
                plans, access_token="token", write_feishu=True
            )
        update.assert_not_called()
        self.assertEqual(results[0]["status"], "no-platform-order")


class ReportTests(unittest.TestCase):
    def test_report_counts_and_round_trips_through_json(self):
        import json

        items = [
            {"record_id": "rec-1", "status": "written"},
            {"record_id": "rec-2", "status": "written"},
            {"record_id": "rec-3", "status": "conflict"},
        ]
        report = order_backfill.build_order_backfill_report(
            store_id="store-1", person="王良希（技术）", lookback_hours=72,
            write_feishu=True, platform_rows=5, search_errors=["creator_b · 待发货：超时"], items=items,
        )
        payload = json.loads(json.dumps(report, ensure_ascii=False))
        self.assertEqual(payload["envelope_type"], "order_backfill_report")
        self.assertEqual(payload["counts"], {"written": 2, "conflict": 1})
        self.assertEqual(payload["platform_rows"], 5)
        self.assertEqual(payload["search_errors"], ["creator_b · 待发货：超时"])
        self.assertTrue(payload["write_feishu"])


if __name__ == "__main__":
    unittest.main()
