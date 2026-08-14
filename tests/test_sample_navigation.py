from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.sample_navigation import (  # noqa: E402
    INSPECT_NAVIGATION_PAGE_JS,
    SAMPLE_REQUEST_URL,
    is_sample_request_href,
    is_seller_order_href,
    navigate_from_seller_home_to_pending,
    navigate_to_sample_request,
    sample_request_url,
    schedule_page_navigation,
    validate_navigation_start,
    validate_sample_request_destination,
)


class SampleNavigationValidationTests(unittest.TestCase):
    def test_inspector_accepts_region_segment_in_seller_hostname(self) -> None:
        self.assertIn(
            "seller(?:\\.[a-z0-9-]+)*\\.tiktokshopglobalselling\\.com",
            INSPECT_NAVIGATION_PAGE_JS,
        )

    def test_rejects_blank_and_login_pages(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "about:blank"):
            validate_navigation_start({"href": "about:blank", "page_type": "unknown"})

        with self.assertRaisesRegex(RuntimeError, "登录页"):
            validate_navigation_start(
                {
                    "href": "https://seller-us.tiktok.com/account/login",
                    "page_type": "login",
                }
            )

    def test_accepts_seller_center_and_existing_sample_page(self) -> None:
        self.assertEqual(
            validate_navigation_start(
                {
                    "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/creator/detail?cid=creator-test",
                    "page_type": "affiliate-center",
                }
            ),
            "affiliate-center",
        )
        self.assertEqual(
            validate_navigation_start(
                {
                    "href": "https://seller-us.tiktok.com/homepage",
                    "page_type": "seller-center",
                }
            ),
            "seller-center",
        )
        self.assertEqual(
            validate_navigation_start(
                {
                    "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_region=US&shop_id=shop-two",
                    "page_type": "sample-request",
                }
            ),
            "sample-request",
        )

    def test_destination_requires_shop_context(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "shop_id"):
            validate_sample_request_destination(
                {
                    "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_region=US"
                }
            )

        context = validate_sample_request_destination(
            {
                "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_region=US&shop_id=shop-two"
            }
        )
        self.assertEqual(context["shop_id"], "shop-two")
        self.assertEqual(context["shop_region"], "US")


class SampleNavigationFlowTests(unittest.TestCase):
    def test_scheduled_navigation_returns_before_location_change(self) -> None:
        execute_script = Mock(return_value={"ok": True, "scheduled": True})

        result = schedule_page_navigation(
            "store-two",
            SAMPLE_REQUEST_URL,
            execute_script_fn=execute_script,
        )

        self.assertTrue(result["scheduled"])
        navigation_script = execute_script.call_args.args[1]
        self.assertIn("setTimeout", navigation_script)
        self.assertIn("location.assign", navigation_script)

    def test_navigates_from_seller_home_and_ensures_pending_tab(self) -> None:
        page_states = iter(
            [
                {
                    "href": "https://seller-us.tiktok.com/homepage",
                    "page_type": "seller-center",
                },
                {
                    "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_region=US&shop_id=shop-two",
                    "page_type": "sample-request",
                },
                {
                    "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_region=US&shop_id=shop-two",
                    "ready": True,
                    "row_count": 1,
                },
            ]
        )
        execute_script = Mock(side_effect=lambda *args, **kwargs: next(page_states))
        navigate_page = Mock(return_value={"ok": True})
        ensure_pending = Mock(return_value={"ok": True, "already": True})

        result = navigate_from_seller_home_to_pending(
            "store-two",
            navigation_timeout=1,
            poll_interval=0.01,
            execute_script_fn=execute_script,
            navigate_page_fn=navigate_page,
            ensure_pending_fn=ensure_pending,
        )

        navigate_page.assert_called_once_with("store-two", SAMPLE_REQUEST_URL)
        ensure_pending.assert_called_once()
        self.assertEqual(result["destination"]["shop_id"], "shop-two")

    def test_existing_sample_page_is_reopened_to_reset_pagination(self) -> None:
        sample_state = {
            "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_region=US&shop_id=shop-two",
            "page_type": "sample-request",
        }
        readiness_state = {
            "href": sample_state["href"],
            "ready": True,
            "row_count": 1,
        }
        navigate_page = Mock()

        result = navigate_from_seller_home_to_pending(
            "store-two",
            navigation_timeout=1,
            poll_interval=0.01,
            execute_script_fn=Mock(side_effect=[sample_state, sample_state, readiness_state]),
            navigate_page_fn=navigate_page,
            ensure_pending_fn=Mock(return_value={"ok": True}),
        )

        navigate_page.assert_called_once_with("store-two", SAMPLE_REQUEST_URL)
        self.assertEqual(result["initial_page_type"], "sample-request")

    def test_sample_request_url_includes_known_shop_id(self) -> None:
        url = sample_request_url(shop_id="shop-two", shop_region="US")
        self.assertIn("shop_id=shop-two", url)
        self.assertIn("shop_region=US", url)

    def test_href_helpers_recognize_sample_and_order_pages(self) -> None:
        self.assertTrue(
            is_sample_request_href(
                "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=1"
            )
        )
        self.assertTrue(
            is_seller_order_href(
                "https://seller.us.tiktokshopglobalselling.com/order?tab=all"
            )
        )
        self.assertFalse(is_sample_request_href("https://seller.us.tiktokshopglobalselling.com/order"))

    def test_already_on_sample_page_skips_navigation(self) -> None:
        current_href = (
            "https://affiliate.tiktokshopglobalselling.com/"
            "affiliate/sample/sample-request?shop_region=US&shop_id=shop-two"
        )
        navigate_page = Mock()

        result = navigate_to_sample_request(
            "store-two",
            shop_id="shop-two",
            execute_script_fn=Mock(return_value={"href": current_href}),
            navigate_page_fn=navigate_page,
        )

        self.assertTrue(result["already"])
        navigate_page.assert_not_called()

    def test_leaves_order_page_with_async_navigation_not_visit_page(self) -> None:
        order_href = "https://seller.us.tiktokshopglobalselling.com/order?tab=all"
        sample_href = (
            "https://affiliate.tiktokshopglobalselling.com/"
            "affiliate/sample/sample-request?shop_region=US&shop_id=shop-two"
        )
        execute_script = Mock(side_effect=[{"href": order_href}, {"href": sample_href}])
        navigate_page = Mock(return_value={"ok": True, "scheduled": True})

        result = navigate_to_sample_request(
            "store-two",
            shop_id="shop-two",
            timeout=1,
            poll_interval=0.01,
            execute_script_fn=execute_script,
            navigate_page_fn=navigate_page,
        )

        self.assertFalse(result["already"])
        self.assertEqual(result["href"], sample_href)
        navigate_page.assert_called_once()
        self.assertIn("shop_id=shop-two", navigate_page.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
