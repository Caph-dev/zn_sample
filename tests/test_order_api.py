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
from lib.page_api import SELLER_LOGISTICS_ENDPOINT, SellerPageContext  # noqa: E402


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

    def test_rejects_payload_without_tracking_value(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "未找到运单号"):
            parse_logistics_payload(
                {"code": 0, "data": {"package_list": [{"status": "delivered"}]}},
                order_id="order-test-001",
            )


class OrderApiAdapterTests(unittest.TestCase):
    @patch("lib.order_api.get_seller_read_json")
    @patch("lib.order_api.get_seller_page_context")
    @patch("lib.order_api.visit_page")
    def test_fetches_logistics_through_allowlisted_get(
        self,
        visit_page,
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
        visit_page.assert_called_once()
        get_read_json.assert_called_once()
        self.assertEqual(get_read_json.call_args.args[1], SELLER_LOGISTICS_ENDPOINT)
        self.assertEqual(
            get_read_json.call_args.kwargs["query"],
            {"main_order_id": "order-test-001"},
        )


if __name__ == "__main__":
    unittest.main()
