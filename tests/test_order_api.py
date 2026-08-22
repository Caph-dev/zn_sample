from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = PROJECT_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.order_api import (  # noqa: E402
    build_logistics_query,
    fetch_tiktok_logistics_details_api,
    fetch_tiktok_logistics_payload,
    fetch_tiktok_tracking_api,
    parse_logistics_details,
    parse_logistics_payload,
)
from lib.order_dom import order_lookup_url  # noqa: E402
from lib.page_api import (  # noqa: E402
    SELLER_APP_NAME,
    SELLER_LOGISTICS_ENDPOINT,
    SellerPageContext,
    _build_request_url,
    build_seller_request_query,
)


def load_logistics_fixture(filename: str) -> dict:
    return json.loads((FIXTURE_DIRECTORY / filename).read_text(encoding="utf-8"))


class OrderLookupUrlTests(unittest.TestCase):
    def test_us_region_uses_seller_us_host(self) -> None:
        url = order_lookup_url("577524130949468533", shop_region="US")
        self.assertIn("https://seller.us.tiktokshopglobalselling.com/order?", url)
        self.assertNotIn("://seller.tiktokshopglobalselling.com/", url)


class SellerLogisticsQueryTests(unittest.TestCase):
    def test_seller_base_query_matches_order_page_not_shop_id_pair(self) -> None:
        context = SellerPageContext(
            href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            shop_id="7496019476093176674",
            shop_region="US",
            page_query={
                "locale": "zh-CN",
                "language": "zh-CN",
                "device_platform": "web",
            },
        )
        query = build_seller_request_query(
            context,
            extra_query={"main_order_id": "577523711101473244"},
        )

        self.assertEqual(query["oec_seller_id"], "7496019476093176674")
        self.assertEqual(query["seller_id"], "7496019476093176674")
        self.assertEqual(query["aid"], "6556")
        self.assertEqual(query["app_name"], SELLER_APP_NAME)
        self.assertEqual(query["main_order_id"], "577523711101473244")
        self.assertEqual(query["locale"], "zh-CN")
        self.assertNotIn("shop_id", query)
        self.assertNotIn("shop_region", query)

    def test_seller_request_url_omits_legacy_shop_query(self) -> None:
        context = SellerPageContext(
            href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            shop_id="7496019476093176674",
            shop_region="US",
        )
        url = _build_request_url(
            SELLER_LOGISTICS_ENDPOINT,
            context=context,
            allowed_endpoints={SELLER_LOGISTICS_ENDPOINT},
            extra_query={"main_order_id": "577523711101473244"},
        )

        self.assertTrue(url.startswith(f"{SELLER_LOGISTICS_ENDPOINT}?"))
        self.assertIn("oec_seller_id=7496019476093176674", url)
        self.assertIn("seller_id=7496019476093176674", url)
        self.assertIn("main_order_id=577523711101473244", url)
        self.assertNotIn("shop_id=", url)
        self.assertNotIn("shop_region=", url)


class OrderApiParserTests(unittest.TestCase):
    def test_builds_frontend_compatible_logistics_query(self) -> None:
        query = build_logistics_query(
            "order-test-001",
            fulfill_unit_ids=["unit-1", "unit-2"],
        )

        self.assertEqual(
            query,
            {
                "main_order_id": "order-test-001",
                "fulfill_unit_ids[0]": "unit-1",
                "fulfill_unit_ids[1]": "unit-2",
            },
        )

    def test_extracts_tracking_number_from_logistics_payload(self) -> None:
        result = parse_logistics_payload(
            {
                "code": 0,
                "data": {
                    "package_list": [
                        {
                            "logistics_info": {
                                "tracking_number": "UUS68E5590171628828"
                            }
                        }
                    ]
                },
            },
            order_id="577523711101473244",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tracking_no"], "UUS68E5590171628828")
        self.assertEqual(result["tracking_raw"], "UUS68E5590171628828")
        self.assertNotIn("delivered_at", result)
        self.assertNotIn("status_category", result)

    def test_uses_sibling_carrier_field_instead_of_guessing_cbt(self) -> None:
        result = parse_logistics_payload(
            {
                "code": 0,
                "data": {
                    "package_list": [
                        {
                            "logistics_info": {
                                "provider_name": "USPS",
                                "tracking_number": "9200190412726311129185",
                            }
                        }
                    ]
                },
            },
            order_id="577524102614586321",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tracking_no"], "9200190412726311129185")
        self.assertEqual(result["tracking_raw"], "USPS, 9200190412726311129185")

    def test_bare_9200_number_stays_unprefixed_without_carrier(self) -> None:
        result = parse_logistics_payload(
            {
                "code": 0,
                "data": {
                    "package_list": [
                        {
                            "logistics_info": {
                                "tracking_number": "9200190412726311129185"
                            }
                        }
                    ]
                },
            },
            order_id="577524102614586321",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tracking_no"], "9200190412726311129185")
        self.assertEqual(result["tracking_raw"], "9200190412726311129185")

    def test_rejects_payload_without_tracking_value(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "未找到运单号"):
            parse_logistics_payload(
                {"code": 0, "data": {"package_list": [{"status": "delivered"}]}},
                order_id="order-test-001",
            )


class LogisticsDetailsParserTests(unittest.TestCase):
    def test_empty_data_is_a_successful_unknown_result(self) -> None:
        result = parse_logistics_details(
            load_logistics_fixture("logistics_empty_data.json"),
            order_id="order-test-001",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["package_count"], 0)
        self.assertEqual(result["status_category"], "unknown")
        self.assertEqual(result["tracking_no"], "")
        self.assertEqual(result["via"], "details-no-tracking")
        self.assertEqual(
            set(result),
            {
                "ok",
                "order_id",
                "tracking_no",
                "tracking_raw",
                "carrier",
                "package_count",
                "status_label",
                "status_category",
                "estimated_delivery_at",
                "delivered_at",
                "last_event_text",
                "last_event_at",
                "fulfill_unit_id",
                "needs_delivery_time_confirmation",
                "missing_fields",
                "raw_payload_hash",
                "via",
            },
        )

    def test_in_transit_uses_verified_paths_and_eta_only(self) -> None:
        result = parse_logistics_details(
            load_logistics_fixture("logistics_in_transit.json"),
            order_id="order-test-001",
        )

        self.assertEqual(result["status_label"], "In transit")
        self.assertEqual(result["status_category"], "in_transit")
        self.assertIsNone(result["delivered_at"])
        self.assertFalse(result["needs_delivery_time_confirmation"])
        self.assertEqual(
            result["estimated_delivery_at"],
            datetime.fromtimestamp(1787630399, tz=timezone.utc),
        )
        self.assertEqual(result["carrier"], "USPS")
        self.assertEqual(result["tracking_no"], "UUS68E5590171628828")
        self.assertEqual(result["last_event_text"], "in-transit-event")

    def test_packed_uses_label_created_domain_mapping(self) -> None:
        payload = load_logistics_fixture("logistics_in_transit.json")
        payload["data"]["package_list"][0]["logistic_detail"]["track_list"] = [
            {
                "title": "Packed",
                "time": 1787172646,
                "content": "packed-event",
                "track_status": 1,
            }
        ]

        result = parse_logistics_details(payload, order_id="order-test-001")

        self.assertEqual(result["status_category"], "label_created")

    def test_out_for_delivery_is_not_delivered(self) -> None:
        result = parse_logistics_details(
            load_logistics_fixture("logistics_out_for_delivery.json"),
            order_id="order-test-001",
        )

        self.assertEqual(result["status_category"], "out_for_delivery")
        self.assertIsNone(result["delivered_at"])
        self.assertFalse(result["needs_delivery_time_confirmation"])

    def test_delivered_uses_latest_event_time_not_eta(self) -> None:
        result = parse_logistics_details(
            load_logistics_fixture("logistics_delivered.json"),
            order_id="order-test-001",
        )

        delivered_event_time = datetime.fromtimestamp(1787456400, tz=timezone.utc)
        estimated_time = datetime.fromtimestamp(1787630399, tz=timezone.utc)
        self.assertEqual(result["status_category"], "delivered")
        self.assertEqual(result["delivered_at"], delivered_event_time)
        self.assertEqual(result["estimated_delivery_at"], estimated_time)
        self.assertNotEqual(result["delivered_at"], result["estimated_delivery_at"])
        self.assertFalse(result["needs_delivery_time_confirmation"])

    def test_delivered_without_event_time_requires_confirmation(self) -> None:
        payload = load_logistics_fixture("logistics_delivered.json")
        del payload["data"]["package_list"][0]["logistic_detail"]["track_list"][
            0
        ]["time"]

        result = parse_logistics_details(payload, order_id="order-test-001")

        self.assertEqual(result["status_category"], "delivered")
        self.assertIsNone(result["delivered_at"])
        self.assertTrue(result["needs_delivery_time_confirmation"])
        self.assertIn("delivered_at", result["missing_fields"])

    def test_invalid_eta_never_becomes_delivery_time(self) -> None:
        payload = load_logistics_fixture("logistics_in_transit.json")
        payload["data"]["package_list"][0][
            "predict_delivery_time_text"
        ] = "tomorrow"

        result = parse_logistics_details(payload, order_id="order-test-001")

        self.assertIsNone(result["estimated_delivery_at"])
        self.assertIsNone(result["delivered_at"])

    def test_main_order_id_is_not_accepted_as_tracking_number(self) -> None:
        order_id = "577523711101473244"
        payload = load_logistics_fixture("logistics_in_transit.json")
        package = payload["data"]["package_list"][0]
        package["tracking_no"] = order_id
        package["main_order_id"] = order_id

        result = parse_logistics_details(payload, order_id=order_id)

        self.assertEqual(result["tracking_no"], "")
        self.assertEqual(result["via"], "details-no-tracking")
        self.assertIn("tracking_no", result["missing_fields"])

    def test_mixed_delivery_progress_uses_weakest_package(self) -> None:
        payload = load_logistics_fixture("logistics_delivered.json")
        in_transit_package = copy.deepcopy(
            load_logistics_fixture("logistics_in_transit.json")["data"][
                "package_list"
            ][0]
        )
        in_transit_package["tracking_no"] = "UUS68E5590171629999"
        payload["data"]["package_list"].append(in_transit_package)

        result = parse_logistics_details(payload, order_id="order-test-001")

        self.assertEqual(result["package_count"], 2)
        self.assertEqual(result["status_category"], "in_transit")
        self.assertIsNone(result["delivered_at"])

    def test_exception_and_delivered_packages_aggregate_to_exception(self) -> None:
        payload = load_logistics_fixture("logistics_delivered.json")
        exception_package = copy.deepcopy(payload["data"]["package_list"][0])
        exception_package["tracking_no"] = "UUS68E5590171629999"
        exception_package["logistic_detail"]["track_list"][0][
            "title"
        ] = "Delivery canceled"
        payload["data"]["package_list"].append(exception_package)

        result = parse_logistics_details(payload, order_id="order-test-001")

        self.assertEqual(result["status_category"], "exception")
        self.assertIsNone(result["delivered_at"])

    def test_return_received_preserves_returned_domain_category(self) -> None:
        payload = load_logistics_fixture("logistics_delivered.json")
        payload["data"]["package_list"][0]["logistic_detail"]["track_list"][0][
            "title"
        ] = "Return received"

        result = parse_logistics_details(payload, order_id="order-test-001")

        self.assertEqual(result["status_category"], "returned")
        self.assertIsNone(result["delivered_at"])

    def test_sensitive_values_do_not_affect_raw_payload_hash(self) -> None:
        payload = load_logistics_fixture("logistics_in_transit.json")
        payload["data"]["receiver_phone"] = "synthetic-phone-one"
        first_result = parse_logistics_details(payload, order_id="order-test-001")
        payload["data"]["receiver_phone"] = "synthetic-phone-two"
        second_result = parse_logistics_details(payload, order_id="order-test-001")

        self.assertEqual(
            first_result["raw_payload_hash"],
            second_result["raw_payload_hash"],
        )


class OrderApiAdapterTests(unittest.TestCase):
    @patch("lib.order_api.fetch_tiktok_logistics_payload")
    def test_fetches_and_parses_logistics_details(self, fetch_payload) -> None:
        fetch_payload.return_value = load_logistics_fixture(
            "logistics_out_for_delivery.json"
        )

        result = fetch_tiktok_logistics_details_api(
            "store-test",
            "order-test-001",
            wait=0,
            retries=0,
        )

        self.assertEqual(result["status_category"], "out_for_delivery")
        fetch_payload.assert_called_once_with(
            "store-test",
            "order-test-001",
            shop_id="",
            shop_region="US",
            fulfill_unit_ids=(),
            wait=0,
            retries=0,
        )

    @patch("lib.order_api.get_seller_read_json")
    @patch("lib.order_api.get_seller_page_context")
    @patch("lib.order_api.navigate_to_url")
    def test_fetches_raw_logistics_payload_through_allowlisted_get(
        self,
        navigate_to_url,
        get_context,
        get_read_json,
    ) -> None:
        expected_payload = load_logistics_fixture("logistics_in_transit.json")
        get_context.return_value = SellerPageContext(
            href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            shop_id="shop-test",
            shop_region="US",
        )
        get_read_json.return_value = expected_payload

        payload = fetch_tiktok_logistics_payload(
            "store-test",
            "order-test-001",
            shop_id="shop-test",
            wait=0,
            retries=0,
        )

        self.assertIs(payload, expected_payload)
        navigate_to_url.assert_called_once()
        self.assertIn(
            "seller.us.tiktokshopglobalselling.com",
            navigate_to_url.call_args.args[1],
        )
        get_read_json.assert_called_once()
        self.assertEqual(get_read_json.call_args.args[1], SELLER_LOGISTICS_ENDPOINT)
        self.assertEqual(
            get_read_json.call_args.kwargs["query"],
            {"main_order_id": "order-test-001"},
        )

    @patch("lib.order_api.get_seller_read_json")
    @patch("lib.order_api.get_seller_page_context")
    @patch("lib.order_api.navigate_to_url")
    def test_fetches_logistics_through_allowlisted_get(
        self,
        navigate_to_url,
        get_context,
        get_read_json,
    ) -> None:
        get_context.return_value = SellerPageContext(
            href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            shop_id="shop-test",
            shop_region="US",
        )
        get_read_json.return_value = {
            "code": 0,
            "data": {
                "package_list": [
                    {"tracking_number": "UUS68E5590171628828"}
                ]
            },
        }

        result = fetch_tiktok_tracking_api(
            "store-test",
            "order-test-001",
            shop_id="shop-test",
            wait=0,
            retries=0,
        )

        self.assertEqual(result["tracking_no"], "UUS68E5590171628828")
        navigate_to_url.assert_called_once()
        self.assertGreaterEqual(navigate_to_url.call_args.kwargs["timeout"], 30)
        self.assertIn(
            "seller.us.tiktokshopglobalselling.com",
            navigate_to_url.call_args.args[1],
        )
        self.assertNotIn(
            "://seller.tiktokshopglobalselling.com/",
            navigate_to_url.call_args.args[1],
        )
        get_read_json.assert_called_once()
        self.assertEqual(get_read_json.call_args.args[1], SELLER_LOGISTICS_ENDPOINT)
        self.assertEqual(
            get_read_json.call_args.kwargs["query"],
            {"main_order_id": "order-test-001"},
        )

    @patch("lib.page_api.zclaw_exec")
    def test_seller_get_script_skips_json_content_type(self, execute) -> None:
        execute.side_effect = [
            {"ok": True, "started": True, "request_id": "request-test"},
            {
                "done": True,
                "ok": True,
                "status": 200,
                "payload": {"code": 0, "data": {"package_list": []}},
            },
        ]
        from lib.page_api import get_seller_read_json

        payload = get_seller_read_json(
            "store-test",
            SELLER_LOGISTICS_ENDPOINT,
            query={"main_order_id": "577523711101473244"},
            context=SellerPageContext(
                href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                shop_id="7496019476093176674",
                shop_region="US",
            ),
        )
        start_script = execute.call_args_list[0].args[1]
        self.assertIn("oec_seller_id=7496019476093176674", start_script)
        self.assertIn("seller_id=7496019476093176674", start_script)
        self.assertNotIn("shop_id=", start_script)
        self.assertNotIn("document.cookie", start_script.lower())
        self.assertIn('const requestMethod = "GET"', start_script)
        self.assertEqual(payload["code"], 0)


if __name__ == "__main__":
    unittest.main()
