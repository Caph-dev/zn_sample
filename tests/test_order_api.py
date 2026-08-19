from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.order_api import (  # noqa: E402
    build_logistics_query,
    fetch_tiktok_tracking_api,
    parse_logistics_payload,
)
from lib.page_api import (  # noqa: E402
    SELLER_APP_NAME,
    SELLER_LOGISTICS_ENDPOINT,
    SellerPageContext,
    _build_request_url,
    build_seller_request_query,
)


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


class OrderApiAdapterTests(unittest.TestCase):
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
