from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.sample_navigation import (  # noqa: E402
    INSPECT_NAVIGATION_PAGE_JS,
    SAMPLE_REQUEST_URL,
    ensure_sample_request_context,
    is_sample_request_href,
    is_seller_order_href,
    is_ziniao_navigation_error_href,
    wait_for_page_href,
    navigate_from_seller_home_to_pending,
    navigate_to_sample_request,
    sample_request_url,
    schedule_page_navigation,
    validate_navigation_start,
    validate_sample_request_destination,
)

SAMPLE_HREF = (
    "https://affiliate.tiktokshopglobalselling.com/"
    "affiliate/sample/sample-request?shop_region=US&shop_id=shop-two"
)
SAMPLE_STATE = {"href": SAMPLE_HREF, "page_type": "sample-request"}
READY_STATE = {"href": SAMPLE_HREF, "ready": True, "row_count": 1}


def _exec_pages(start: dict, dest: dict | None = None, ready: dict | None = None):
    seen_start = {"done": False}
    destination = dest or SAMPLE_STATE
    readiness = ready or READY_STATE

    def exec_fn(_store_id, script, **_kwargs):
        if "hasEmptyState" in script:
            return readiness
        if not seen_start["done"]:
            seen_start["done"] = True
            return start
        return destination

    return exec_fn


class SampleNavigationValidationTests(unittest.TestCase):
    def test_inspector_accepts_region_segment_in_seller_hostname(self) -> None:
        self.assertIn(
            "seller(?:\\.[a-z0-9-]+)*\\.tiktokshopglobalselling\\.com",
            INSPECT_NAVIGATION_PAGE_JS,
        )

    def test_inspector_treats_any_logged_in_seller_host_as_seller_center(self) -> None:
        self.assertNotIn("sellerHost && sellerCenterEvidence", INSPECT_NAVIGATION_PAGE_JS)
        self.assertNotIn("sellerCenterEvidence", INSPECT_NAVIGATION_PAGE_JS)
        self.assertIn("sellerHost", INSPECT_NAVIGATION_PAGE_JS)
        self.assertIn("? 'seller-center'", INSPECT_NAVIGATION_PAGE_JS)

    def test_rejects_blank_and_login_pages(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "about:blank"):
            validate_navigation_start({"href": "about:blank", "page_type": "unknown"})
        with self.assertRaisesRegex(RuntimeError, "登录"):
            validate_navigation_start(
                {
                    "href": "https://seller.us.tiktokshopglobalselling.com/account/login",
                    "page_type": "login",
                }
            )

        with self.assertRaisesRegex(RuntimeError, "登录页"):
            validate_navigation_start(
                {
                    "href": "https://seller-us.tiktok.com/account/login",
                    "page_type": "login",
                }
            )

    def test_rejects_unrecognized_hosts(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "无法确认"):
            validate_navigation_start(
                {
                    "href": "https://www.google.com/",
                    "page_type": "unknown",
                    "title": "Google",
                }
            )

    def test_accepts_common_seller_subpages(self) -> None:
        for href in (
            "https://seller.us.tiktokshopglobalselling.com/homepage",
            "https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            "https://seller.us.tiktokshopglobalselling.com/product/list",
            "https://seller.us.tiktokshopglobalselling.com/compass/dashboard",
        ):
            self.assertEqual(
                validate_navigation_start({"href": href, "page_type": "seller-center"}),
                "seller-center",
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
        self.assertIn("location.replace", navigation_script)
        self.assertNotIn("location.assign", navigation_script)

    def test_navigates_from_seller_home_and_ensures_pending_tab(self) -> None:
        navigate_page = Mock(return_value={"ok": True})
        ensure_pending = Mock(return_value={"ok": True, "already": True})

        result = navigate_from_seller_home_to_pending(
            "store-two",
            navigation_timeout=1,
            poll_interval=0.01,
            execute_script_fn=_exec_pages(
                {
                    "href": "https://seller-us.tiktok.com/homepage",
                    "page_type": "seller-center",
                }
            ),
            navigate_page_fn=navigate_page,
            ensure_pending_fn=ensure_pending,
        )

        navigate_page.assert_called_once_with("store-two", SAMPLE_REQUEST_URL)
        ensure_pending.assert_called_once()
        self.assertEqual(result["destination"]["shop_id"], "shop-two")

    @patch("lib.sample_navigation.time.sleep")
    def test_navigates_from_order_and_product_pages(self, _sleep) -> None:
        for start_href in (
            "https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            "https://seller.us.tiktokshopglobalselling.com/product/list",
            "https://seller.tiktokshopglobalselling.com/order?tab=all",
        ):
            navigate_page = Mock(return_value={"ok": True})
            result = navigate_from_seller_home_to_pending(
                "store-two",
                navigation_timeout=1,
                poll_interval=0.01,
                execute_script_fn=_exec_pages(
                    {"href": start_href, "page_type": "seller-center"}
                ),
                navigate_page_fn=navigate_page,
                ensure_pending_fn=Mock(return_value={"ok": True}),
            )
            navigate_page.assert_called_once_with("store-two", SAMPLE_REQUEST_URL)
            self.assertEqual(result["initial_page_type"], "seller-center")
            self.assertEqual(result["destination"]["shop_id"], "shop-two")

    def test_ensure_context_skips_when_already_on_sample_request(self) -> None:
        sample_state = {
            "href": (
                "https://affiliate.tiktokshopglobalselling.com/"
                "affiliate/sample/sample-request?shop_region=US&shop_id=shop-two"
            ),
            "page_type": "sample-request",
        }
        navigate_page = Mock()
        result = ensure_sample_request_context(
            "store-two",
            force_reload=False,
            execute_script_fn=Mock(return_value=sample_state),
            navigate_page_fn=navigate_page,
        )
        navigate_page.assert_not_called()
        self.assertTrue(result["already"])
        self.assertEqual(result["destination"]["shop_id"], "shop-two")

    @patch("lib.sample_navigation.time.sleep")
    def test_ensure_context_waits_for_shop_id_from_seller_subpage(self, _sleep) -> None:
        navigate_page = Mock(return_value={"ok": True})
        result = ensure_sample_request_context(
            "store-two",
            force_reload=False,
            navigation_timeout=1,
            poll_interval=0.01,
            execute_script_fn=_exec_pages(
                {
                    "href": "https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    "page_type": "seller-center",
                }
            ),
            navigate_page_fn=navigate_page,
        )
        navigate_page.assert_called_once_with("store-two", SAMPLE_REQUEST_URL)
        self.assertFalse(result["already"])
        self.assertEqual(result["destination"]["shop_id"], "shop-two")

    @patch("lib.sample_navigation.time.sleep")
    def test_ignores_transient_sample_url_if_order_page_bounces_back(self, _sleep) -> None:
        order_state = {
            "href": (
                "https://seller.tiktokshopglobalselling.com/order"
                "?main_order_id[]=577527058437214806&tab=all"
            ),
            "page_type": "seller-center",
        }
        states = iter([order_state, SAMPLE_STATE, order_state, SAMPLE_STATE, SAMPLE_STATE])
        result = ensure_sample_request_context(
            "store-two",
            force_reload=False,
            navigation_timeout=1,
            poll_interval=0.01,
            execute_script_fn=Mock(side_effect=lambda *a, **k: next(states)),
            navigate_page_fn=Mock(return_value={"ok": True}),
        )
        self.assertEqual(result["destination"]["shop_id"], "shop-two")

    @patch("lib.sample_navigation.time.sleep")
    def test_times_out_if_sample_url_never_stabilizes(self, _sleep) -> None:
        order_state = {
            "href": "https://seller.tiktokshopglobalselling.com/order?tab=all",
            "page_type": "seller-center",
        }
        states = [order_state, SAMPLE_STATE]
        states.extend([order_state] * 20)
        cursor = iter(states)
        with self.assertRaisesRegex(RuntimeError, "超时"):
            ensure_sample_request_context(
                "store-two",
                force_reload=False,
                navigation_timeout=0.2,
                poll_interval=0.01,
                execute_script_fn=Mock(side_effect=lambda *a, **k: next(cursor)),
                navigate_page_fn=Mock(return_value={"ok": True}),
            )

    def test_existing_sample_page_is_reopened_to_reset_pagination(self) -> None:
        navigate_page = Mock()

        result = navigate_from_seller_home_to_pending(
            "store-two",
            navigation_timeout=1,
            poll_interval=0.01,
            execute_script_fn=_exec_pages(SAMPLE_STATE),
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
        self.assertTrue(
            is_seller_order_href(
                "https://seller.tiktokshopglobalselling.com/order"
                "?main_order_id[]=577527058437214806&tab=all"
            )
        )
        self.assertFalse(is_sample_request_href("https://seller.us.tiktokshopglobalselling.com/order"))
        self.assertTrue(
            is_ziniao_navigation_error_href(
                "chrome-extension://edhdkldonkbhojdeilbcmhpplfiomheo/error.html"
                "?url=https%3A%2F%2Fseller.tiktokshopglobalselling.com%2Forder"
            )
        )
        self.assertFalse(
            is_ziniao_navigation_error_href(
                "https://seller.us.tiktokshopglobalselling.com/order?tab=all"
            )
        )

    def test_wait_for_page_href_fails_fast_on_ziniao_error_page(self) -> None:
        error_href = (
            "chrome-extension://edhdkldonkbhojdeilbcmhpplfiomheo/error.html"
            "?url=https%3A%2F%2Fseller.tiktokshopglobalselling.com%2Forder"
        )
        execute = Mock(return_value={"href": error_href})
        with self.assertRaisesRegex(RuntimeError, "紫鸟拦截"):
            wait_for_page_href(
                "store-two",
                is_seller_order_href,
                timeout=1,
                poll_interval=0.01,
                execute_script_fn=execute,
            )
        self.assertEqual(execute.call_count, 1)

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
