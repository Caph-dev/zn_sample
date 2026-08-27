from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.feishu_bitable import (  # noqa: E402
    FeishuBitableError,
    describe_cooperation_transition,
    feishu_shipping_already_current,
    DEFAULT_RECORD_OWNER,
    build_product_id_to_sku_map,
    build_shipping_fields,
    create_creator_relation_record,
    find_duplicate_record,
    find_duplicate_records,
    index_pending_ship_records,
    pending_ship_lookup_key,
    resolve_duplicate_record,
    resolve_sample_product_for_row,
    search_pending_ship_records,
    update_record_order_no,
    plan_cooperation_status_transition,
    update_record_cooperation_status,
    COOPERATION_STATUS_COMPLETED,
    COOPERATION_STATUS_UNPUBLISHED,
)


class ProductIdToSkuMapTests(unittest.TestCase):
    hero_data = {
        "rows": [
            {
                "sku": "328",
                "is_hero": True,
                "product_id": "1732060411527205730",
            },
            {
                "sku": "P001",
                "is_hero": False,
                "product_id": "1732117543281857378",
            },
        ]
    }

    def test_default_map_excludes_non_hero_rows(self) -> None:
        mapping = build_product_id_to_sku_map(self.hero_data)
        self.assertEqual(mapping, {"1732060411527205730": "328"})
        self.assertNotIn("1732117543281857378", mapping)

    def test_full_catalog_map_keeps_non_hero_for_logistics(self) -> None:
        mapping = build_product_id_to_sku_map(self.hero_data, hero_only=False)
        self.assertEqual(mapping["1732117543281857378"], "P001")

    def test_resolve_rejects_non_hero_product_id(self) -> None:
        result = resolve_sample_product_for_row(
            {"product_id": "1732117543281857378"},
            product_id_to_sku=build_product_id_to_sku_map(self.hero_data),
            sample_product_options=["328", "P001"],
        )
        self.assertFalse(result["ok"])
        self.assertIn("是否主推=是", result["reason"])


class PendingShipSearchTests(unittest.TestCase):
    def test_lookup_key_is_handle_plus_product(self) -> None:
        self.assertEqual(
            pending_ship_lookup_key("CeliaDailyDeals", "P001"),
            ("celiadailydeals", "P001"),
        )

    def test_index_keeps_pending_ship_without_tracking(self) -> None:
        records = [
            {
                "record_id": "old-with-track",
                "fields": {
                    "红人ID": "alice",
                    "寄样产品": "328",
                    "快递单号": "UUS1",
                    "合作状态": ["待发货"],
                },
            },
            {
                "record_id": "fresh-empty",
                "fields": {
                    "红人ID": "alice",
                    "寄样产品": "328",
                    "合作状态": ["待发货"],
                },
            },
        ]
        indexed = index_pending_ship_records(records)
        self.assertEqual(indexed[("alice", "328")]["record_id"], "fresh-empty")

    def test_search_pending_ship_filters_created_time_and_status(self) -> None:
        since = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
        response = {
            "code": 0,
            "data": {
                "items": [
                    {
                        "record_id": "too-old",
                        "created_time": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
                        "fields": {"红人ID": "old", "寄样产品": "328", "合作状态": ["待发货"]},
                    },
                    {
                        "record_id": "fresh",
                        "created_time": int(datetime(2026, 8, 13, tzinfo=timezone.utc).timestamp() * 1000),
                        "fields": {"红人ID": "new", "寄样产品": "328", "合作状态": ["待发货"]},
                    },
                ],
                "has_more": False,
            },
        }
        with patch("lib.feishu_bitable._http_json", return_value=response) as request:
            items = search_pending_ship_records("token", since=since)

        body = request.call_args.kwargs["body"]
        self.assertTrue(body["automatic_fields"])
        self.assertEqual(
            body["filter"]["conditions"][0]["value"],
            ["待发货"],
        )
        self.assertEqual([item["record_id"] for item in items], ["fresh"])


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


class DuplicateRecordResolutionTests(unittest.TestCase):
    def test_unique_match_returns_the_only_record(self) -> None:
        response = {
            "code": 0,
            "data": {
                "items": [{"record_id": "rec-unique", "fields": {}}],
                "has_more": False,
            },
        }

        with patch("lib.feishu_bitable._http_json", return_value=response):
            result = resolve_duplicate_record(
                "token-test",
                creator_handle="alice",
                sample_product="328",
            )

        self.assertEqual(result["status"], "unique-match")
        self.assertEqual(result["record"]["record_id"], "rec-unique")

    def test_multiple_matches_are_ambiguous_across_pages(self) -> None:
        responses = [
            {
                "code": 0,
                "data": {
                    "items": [{"record_id": "rec-one", "fields": {}}],
                    "has_more": True,
                    "page_token": "next-page",
                },
            },
            {
                "code": 0,
                "data": {
                    "items": [{"record_id": "rec-two", "fields": {}}],
                    "has_more": False,
                },
            },
        ]

        with patch("lib.feishu_bitable._http_json", side_effect=responses) as request:
            result = resolve_duplicate_record(
                "token-test",
                creator_handle="alice",
                sample_product="328",
            )

        self.assertEqual(result["status"], "ambiguous-match")
        self.assertEqual(result["record_ids"], ["rec-one", "rec-two"])
        self.assertEqual(request.call_args_list[1].kwargs["body"]["page_token"], "next-page")

    def test_legacy_duplicate_helper_does_not_choose_ambiguous_record(self) -> None:
        response = {
            "code": 0,
            "data": {
                "items": [
                    {"record_id": "rec-one", "fields": {}},
                    {"record_id": "rec-two", "fields": {}},
                ],
                "has_more": False,
            },
        }

        with patch("lib.feishu_bitable._http_json", return_value=response):
            record = find_duplicate_record(
                "token-test",
                creator_handle="alice",
                sample_product="328",
            )

        self.assertIsNone(record)

    def test_duplicate_search_requires_page_token_when_more_pages_exist(self) -> None:
        response = {
            "code": 0,
            "data": {"items": [], "has_more": True, "page_token": ""},
        }

        with patch("lib.feishu_bitable._http_json", return_value=response):
            with self.assertRaisesRegex(FeishuBitableError, "page_token"):
                find_duplicate_records(
                    "token-test",
                    creator_handle="alice",
                    sample_product="328",
                )


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


class CooperationStatusTransitionTests(unittest.TestCase):
    def test_pending_post_can_become_completed(self) -> None:
        plan = plan_cooperation_status_transition(
            current_status="待发布",
            target_status=COOPERATION_STATUS_COMPLETED,
        )
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["fields"]["合作状态"], ["已完成"])

    def test_pending_post_can_become_unpublished(self) -> None:
        plan = plan_cooperation_status_transition(
            current_status="待发布",
            target_status=COOPERATION_STATUS_UNPUBLISHED,
        )
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["fields"]["合作状态"], ["未发布"])

    def test_completed_is_not_reverted(self) -> None:
        plan = plan_cooperation_status_transition(
            current_status="已完成",
            target_status=COOPERATION_STATUS_UNPUBLISHED,
        )
        self.assertEqual(plan["status"], "preserved")
        self.assertEqual(plan["fields"], {})

    def test_pending_ship_cannot_jump_to_completed(self) -> None:
        plan = plan_cooperation_status_transition(
            current_status="待发货",
            target_status=COOPERATION_STATUS_COMPLETED,
        )
        self.assertEqual(plan["status"], "blocked-unexpected-status")

    def test_update_writes_then_rereads_without_hitting_network(self) -> None:
        with patch("lib.feishu_bitable._http_json") as request:
            request.side_effect = [
                {
                    "code": 0,
                    "data": {"record": {"record_id": "rec-1", "fields": {"合作状态": ["待发布"]}}},
                },
                {"code": 0, "data": {"record": {"record_id": "rec-1"}}},
                {
                    "code": 0,
                    "data": {"record": {"record_id": "rec-1", "fields": {"合作状态": ["已完成"]}}},
                },
            ]
            result = update_record_cooperation_status(
                "token-test",
                "rec-1",
                COOPERATION_STATUS_COMPLETED,
            )
        self.assertEqual(result["status"], "written")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(
            request.call_args_list[1].kwargs["body"]["fields"],
            {"合作状态": ["已完成"]},
        )


class DescribeCooperationTransitionTests(unittest.TestCase):
    def test_common_codes_are_chinese(self) -> None:
        self.assertIn("待发布", describe_cooperation_transition("already-pending-post"))
        self.assertIn("已发布", describe_cooperation_transition("preserved:已发布"))
        self.assertIn("不回退", describe_cooperation_transition("preserved:已发布"))
        self.assertIn("待发货", describe_cooperation_transition("待发货->待发布"))
        self.assertNotIn("already-pending-post", describe_cooperation_transition("already-pending-post"))
        self.assertNotIn("preserved:", describe_cooperation_transition("preserved:已发布"))

    def test_same_track_and_pending_post_is_already_current(self) -> None:
        plan = build_shipping_fields(
            order_no="order-1",
            tracking_raw="tracking-1",
            current={
                "fields": {
                    "快递单号": "tracking-1",
                    "合作状态": ["待发布"],
                }
            },
        )
        self.assertTrue(feishu_shipping_already_current(plan, "tracking-1"))
        self.assertFalse(feishu_shipping_already_current(plan, "tracking-new"))


class UpdateRecordOrderNoTests(unittest.TestCase):
    def test_writes_order_field_when_current_is_empty(self) -> None:
        with patch("lib.feishu_bitable._http_json") as request:
            request.side_effect = [
                {
                    "code": 0,
                    "data": {"record": {"record_id": "rec-1", "fields": {}}},
                },
                {"code": 0, "data": {"record": {"record_id": "rec-1"}}},
                {
                    "code": 0,
                    "data": {
                        "record": {
                            "record_id": "rec-1",
                            "fields": {"订单号": "577532709908747188"},
                        }
                    },
                },
            ]
            result = update_record_order_no(
                "token-test",
                "rec-1",
                "577532709908747188",
            )

        self.assertEqual(result["status"], "written")
        put_call = request.call_args_list[1]
        self.assertEqual(put_call.args[0], "PUT")
        self.assertEqual(
            put_call.kwargs["body"]["fields"],
            {"订单号": "577532709908747188"},
        )
        self.assertEqual(request.call_count, 3)

    def test_unchanged_when_current_is_same(self) -> None:
        with patch("lib.feishu_bitable._http_json") as request:
            request.return_value = {
                "code": 0,
                "data": {
                    "record": {
                        "record_id": "rec-1",
                        "fields": {"订单号": "577532709908747188"},
                    }
                },
            }
            result = update_record_order_no(
                "token-test",
                "rec-1",
                "577532709908747188",
            )

        request.assert_called_once()
        self.assertEqual(result["status"], "unchanged")

    def test_skips_when_current_is_different(self) -> None:
        with patch("lib.feishu_bitable._http_json") as request:
            request.return_value = {
                "code": 0,
                "data": {
                    "record": {
                        "record_id": "rec-1",
                        "fields": {"订单号": "577599999999999999"},
                    }
                },
            }
            result = update_record_order_no(
                "token-test",
                "rec-1",
                "577532709908747188",
            )

        request.assert_called_once()
        self.assertEqual(result["status"], "skipped-existing-different")
        self.assertEqual(result["current_order"], "577599999999999999")

    def test_reports_write_uncertain_when_post_write_value_differs(self) -> None:
        with patch("lib.feishu_bitable._http_json") as request:
            request.side_effect = [
                {
                    "code": 0,
                    "data": {"record": {"record_id": "rec-1", "fields": {}}},
                },
                {"code": 0, "data": {"record": {"record_id": "rec-1"}}},
                {
                    "code": 0,
                    "data": {
                        "record": {
                            "record_id": "rec-1",
                            "fields": {"订单号": "577599999999999999"},
                        }
                    },
                },
            ]
            result = update_record_order_no(
                "token-test",
                "rec-1",
                "577532709908747188",
            )

        self.assertEqual(result["status"], "write-uncertain")
        self.assertEqual(result["current_order"], "577599999999999999")


if __name__ == "__main__":
    unittest.main()
