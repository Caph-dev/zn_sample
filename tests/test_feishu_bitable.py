from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.feishu_bitable import (  # noqa: E402
    DEFAULT_RECORD_OWNER,
    build_shipping_fields,
    create_creator_relation_record,
)


class CreateCreatorRelationRecordTests(unittest.TestCase):
    def test_new_record_assigns_default_owner_and_pending_ship_status(self) -> None:
        response = {
            "code": 0,
            "data": {"record": {"record_id": "record-test"}},
        }

        with patch("lib.feishu_bitable._http_json", return_value=response) as request:
            result = create_creator_relation_record(
                "test-access-token",
                creator_handle="creator_handle",
                followers_raw="3000",
                fulfillment_raw="88%",
                sample_product="P001",
            )

        request_body = request.call_args.kwargs["body"]
        fields = request_body["fields"]
        self.assertEqual(fields["人员"], DEFAULT_RECORD_OWNER)
        self.assertEqual(fields["人员"], "王良希（技术）")
        self.assertEqual(fields["合作状态"], ["待发货"])
        self.assertFalse(fields["是否已寄样"])
        self.assertEqual(result["record_id"], "record-test")


class BuildShippingFieldsTests(unittest.TestCase):
    def test_tracking_transitions_pending_ship_to_pending_post(self) -> None:
        result = build_shipping_fields(
            order_no="order-1",
            tracking_raw="tracking-1",
            current={"fields": {"合作状态": ["待发货"]}},
        )

        self.assertFalse(result["skip_tracking"])
        self.assertEqual(result["fields"]["订单号"], "order-1")
        self.assertEqual(result["fields"]["快递单号"], "tracking-1")
        self.assertTrue(result["fields"]["是否已寄样"])
        self.assertEqual(result["fields"]["合作状态"], ["待发布"])
        self.assertEqual(result["status_transition"], "待发货->待发布")

    def test_cbt_prefix_is_same_tracking_as_bare_number(self) -> None:
        result = build_shipping_fields(
            order_no="577524102614586321",
            tracking_raw="CBT, 9200190412726311129185",
            current={
                "fields": {
                    "订单号": "577524102614586321",
                    "快递单号": "9200190412726311129185",
                    "合作状态": ["待发布"],
                }
            },
        )

        self.assertFalse(result["skip_tracking"])
        self.assertEqual(result["fields"]["快递单号"], "CBT, 9200190412726311129185")
        self.assertEqual(result["status_transition"], "already-pending-post")

    def test_same_tracking_number_repairs_pending_ship_status(self) -> None:
        result = build_shipping_fields(
            order_no="order-1",
            tracking_raw="tracking-1",
            current={
                "fields": {
                    "订单号": "order-1",
                    "快递单号": "tracking-1",
                    "合作状态": ["待发货"],
                }
            },
        )

        self.assertFalse(result["skip_tracking"])
        self.assertEqual(result["fields"]["合作状态"], ["待发布"])

    def test_different_existing_tracking_skips_all_logistics_updates(self) -> None:
        result = build_shipping_fields(
            order_no="order-new",
            tracking_raw="tracking-new",
            language="英语",
            current={
                "fields": {
                    "订单号": "order-old",
                    "快递单号": "tracking-old",
                    "合作状态": ["待发货"],
                }
            },
        )

        self.assertTrue(result["skip_tracking"])
        self.assertEqual(result["fields"], {})
        self.assertEqual(result["status_transition"], "skipped-different-tracking")

    def test_published_status_is_not_reverted(self) -> None:
        result = build_shipping_fields(
            order_no="order-1",
            tracking_raw="tracking-1",
            current={"fields": {"合作状态": ["已发布"]}},
        )

        self.assertEqual(result["fields"]["快递单号"], "tracking-1")
        self.assertTrue(result["fields"]["是否已寄样"])
        self.assertNotIn("合作状态", result["fields"])
        self.assertEqual(result["status_transition"], "preserved:已发布")

    def test_mixed_status_with_published_is_not_reverted(self) -> None:
        result = build_shipping_fields(
            order_no="order-1",
            tracking_raw="tracking-1",
            current={"fields": {"合作状态": ["待发货", "已发布"]}},
        )

        self.assertNotIn("合作状态", result["fields"])
        self.assertEqual(result["status_transition"], "preserved:待发货,已发布")

    def test_empty_tracking_does_not_advance_status(self) -> None:
        result = build_shipping_fields(
            order_no="order-1",
            tracking_raw="",
            current={"fields": {"合作状态": ["待发货"]}},
        )

        self.assertNotIn("是否已寄样", result["fields"])
        self.assertNotIn("合作状态", result["fields"])
        self.assertEqual(result["status_transition"], "not-applicable")


if __name__ == "__main__":
    unittest.main()
