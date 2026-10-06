"""Source navigation cases migrated to mock-only target transport tests."""
from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib import target_invitation_navigation as navigation  # noqa: E402

MIGRATED_HREF = (
    f"https://{navigation.AFFILIATE_CENTER_HOST}{navigation.TARGET_INVITATION_PATH}"
    "?shop_region=US&route_migration=1&shop_id=shop-two&tab=1"
)
LEGACY_HREF = (
    f"https://{navigation.AFFILIATE_CENTER_HOST}{navigation.LEGACY_TARGET_INVITATION_PATH}"
    "?shop_region=US&shop_id=shop-two"
)
SELLER_HREF = "https://seller.us.tiktokshopglobalselling.com/order?tab=all"
DESTINATION_STATE = {"ok": True, "href": MIGRATED_HREF, "page_type": "target-invitation"}


class OfflineNavigationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.sleep = self.context.enter_context(patch.object(navigation.time, "sleep"))
        self.execute = self.context.enter_context(patch.object(
            navigation, "zclaw_exec", side_effect=AssertionError("No browser calls in navigation tests"),
        ))
        self.context.enter_context(patch("lib.zclaw.zclaw_invoke", side_effect=AssertionError("No CLI allowed")))
        self.context.enter_context(patch(
            "lib.target_invitation_dom.zclaw_exec", side_effect=AssertionError("Inject ongoing mock explicitly"),
        ))


class NavigationValidationTests(OfflineNavigationTestCase):
    def test_source_storage_and_region_seller_host_contracts_are_preserved(self) -> None:
        self.assertIn("ecom_seller_base_account_info", navigation.INSPECT_NAVIGATION_PAGE_JS)
        self.assertIn("current_shop_region", navigation.INSPECT_NAVIGATION_PAGE_JS)
        self.assertIn("-affiliate-selected-shop-region", navigation.INSPECT_NAVIGATION_PAGE_JS)
        self.assertIn("seller(?:\\.[a-z0-9-]+)*\\.tiktokshopglobalselling\\.com", navigation.INSPECT_NAVIGATION_PAGE_JS)

    def test_rejects_blank_login_error_and_unrelated_hosts(self) -> None:
        for state in ({"href": "about:blank", "page_type": "unknown"},
                      {"href": SELLER_HREF, "page_type": "login"},
                      {"href": "chrome-error://chromewebdata/", "page_type": "seller-center"},
                      {"href": "https://example.invalid/order", "page_type": "seller-center"},
                      {"href": "https://affiliate.tiktokshopglobalselling.com.evil.invalid/", "page_type": "affiliate-center"}):
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                navigation.validate_navigation_start(state)

    def test_accepts_source_logged_in_seller_subpages(self) -> None:
        for path in ("homepage", "order?tab=all", "product/list", "compass/dashboard"):
            self.assertEqual(navigation.validate_navigation_start({
                "href": f"https://seller.us.tiktokshopglobalselling.com/{path}", "page_type": "seller-center",
            }), "seller-center")
        self.assertEqual(navigation.validate_navigation_start(DESTINATION_STATE), "target-invitation")

    def test_only_proven_legacy_and_migrated_paths_are_accepted(self) -> None:
        for href in (MIGRATED_HREF, LEGACY_HREF):
            self.assertTrue(navigation.is_target_invitation_href(href))
            self.assertEqual(navigation.validate_target_invitation_destination({"href": href})["shop_id"], "shop-two")
        for href in (MIGRATED_HREF.replace(navigation.TARGET_INVITATION_PATH, "/unknown/target-invitation"),
                     MIGRATED_HREF.replace("https:", "http:"),
                     MIGRATED_HREF.replace(navigation.TARGET_INVITATION_PATH, "/affiliate/sample/sample-request")):
            with self.subTest(href=href):
                self.assertFalse(navigation.is_target_invitation_href(href))

    def test_destination_requires_unique_nonempty_shop_context(self) -> None:
        for href in (MIGRATED_HREF.replace("&shop_id=shop-two", ""),
                     MIGRATED_HREF.replace("shop_region=US&", ""),
                     MIGRATED_HREF + "&shop_id=other", MIGRATED_HREF + "&shop_region=GB"):
            with self.subTest(href=href), self.assertRaises(RuntimeError):
                navigation.validate_target_invitation_destination({"href": href})
        with self.assertRaises(RuntimeError):
            navigation.validate_target_invitation_destination({"href": MIGRATED_HREF, "page_type": "login"})

    def test_urls_and_shop_context_follow_source_new_route_fallback(self) -> None:
        self.assertIn(navigation.TARGET_INVITATION_PATH, navigation.resolve_target_invitation_url(shop_id="abc"))
        self.assertIn("shop_id=abc", navigation.resolve_target_invitation_url(shop_id="abc"))
        self.assertIn(navigation.LEGACY_TARGET_INVITATION_PATH, navigation.resolve_target_invitation_url())
        self.assertEqual(navigation.shop_context_from_page_state({
            "href": SELLER_HREF, "shop_id": "shop-two", "shop_region": "us",
        }), ("shop-two", "US"))
        self.assertEqual(navigation.shop_context_from_page_state({"href": SELLER_HREF}), ("", "US"))
        with self.assertRaises(ValueError):
            navigation.target_invitation_url(path="/unproven")


class NavigationFlowTests(OfflineNavigationTestCase):
    def run_flow(self, states: list[dict], *, readiness: dict | None = None, **fields):
        execute = Mock(side_effect=states)
        navigate = Mock(return_value={"ok": True})
        ongoing = Mock(return_value={"ok": True, "already": True})
        ready = Mock(return_value={"ready": True, "href": MIGRATED_HREF} if readiness is None else readiness)
        result = navigation.navigate_from_seller_home_to_ongoing(
            "store-two", execute_script_fn=execute, navigate_page_fn=navigate,
            ensure_ongoing_fn=ongoing, wait_ready_fn=ready, **fields,
        )
        self.execute.assert_not_called()
        return result, execute, navigate, ongoing, ready

    def test_already_target_page_skips_location_assign(self) -> None:
        result, execute, navigate, ongoing, ready = self.run_flow([DESTINATION_STATE])
        self.assertTrue(result["already"])
        self.assertEqual(result["destination"]["shop_id"], "shop-two")
        execute.assert_called_once()
        navigate.assert_not_called()
        ongoing.assert_called_once()
        ready.assert_called_once()

    def test_seller_order_page_navigates_and_waits_for_platform_shop_context(self) -> None:
        result, _, navigate, _, _ = self.run_flow([
            {"href": SELLER_HREF, "page_type": "seller-center", "shop_id": "shop-two", "shop_region": "US"},
            {"href": MIGRATED_HREF.replace("&shop_id=shop-two", ""), "page_type": "target-invitation"},
            DESTINATION_STATE,
        ])
        navigate.assert_called_once_with("store-two", navigation.target_invitation_url(shop_id="shop-two"))
        self.assertFalse(result["already"])
        self.assertEqual(result["initial_page_type"], "seller-center")
        self.assertEqual(result["destination"]["shop_id"], "shop-two")

    def test_missing_shop_id_uses_legacy_url_and_accepts_route_migration(self) -> None:
        result, _, navigate, _, _ = self.run_flow([
            {"href": SELLER_HREF, "page_type": "seller-center"}, DESTINATION_STATE,
        ])
        navigate.assert_called_once_with("store-two", navigation.target_invitation_url(path=navigation.LEGACY_TARGET_INVITATION_PATH))
        self.assertIn("route_migration=1", result["destination"]["href"])

    def test_legacy_destination_is_still_valid(self) -> None:
        result, _, navigate, _, _ = self.run_flow([
            {"href": SELLER_HREF, "page_type": "seller-center"},
            {"href": LEGACY_HREF, "page_type": "target-invitation"},
        ], readiness={"ready": True, "href": LEGACY_HREF})
        self.assertFalse(result["already"])
        self.assertEqual(result["destination"]["href"], LEGACY_HREF)
        navigate.assert_called_once()

    def test_start_blockers_do_not_navigate_or_select_a_tab(self) -> None:
        for state in ({"href": "about:blank", "page_type": "unknown"},
                      {"href": SELLER_HREF, "page_type": "login"}):
            navigate = Mock()
            ongoing = Mock()
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                navigation.navigate_from_seller_home_to_ongoing(
                    "store-two", execute_script_fn=Mock(return_value=state),
                    navigate_page_fn=navigate, ensure_ongoing_fn=ongoing,
                )
            navigate.assert_not_called()
            ongoing.assert_not_called()

    def test_shop_change_before_or_during_readiness_is_rejected(self) -> None:
        initial_state = {"href": SELLER_HREF, "page_type": "seller-center", "shop_id": "shop-two"}
        for destination, ready_state in (
            ({**DESTINATION_STATE, "href": MIGRATED_HREF.replace("shop-two", "other-shop")},
             {"ready": True, "href": MIGRATED_HREF}),
            (DESTINATION_STATE, {"ready": True, "href": MIGRATED_HREF.replace("shop-two", "other-shop")}),
        ):
            with self.subTest(destination=destination, readiness=ready_state), self.assertRaisesRegex(RuntimeError, "shop"):
                self.run_flow([initial_state, destination], readiness=ready_state)

    def test_readiness_failure_on_existing_list_does_not_renavigate(self) -> None:
        navigate = Mock()
        ongoing = Mock()
        with self.assertRaisesRegex(RuntimeError, "not verified"):
            navigation.navigate_from_seller_home_to_ongoing(
                "store-two", execute_script_fn=Mock(return_value=DESTINATION_STATE),
                navigate_page_fn=navigate, ensure_ongoing_fn=ongoing,
                wait_ready_fn=Mock(return_value={"ready": False}),
            )
        navigate.assert_not_called()
        ongoing.assert_not_called()

    def test_navigation_timeout_never_selects_tab_or_restarts_store(self) -> None:
        execute = Mock(side_effect=[{"href": SELLER_HREF, "page_type": "seller-center"}, {"href": SELLER_HREF}])
        ongoing = Mock()
        with patch.object(navigation.time, "monotonic", side_effect=[0.0, 0.1, 2.0]), self.assertRaisesRegex(RuntimeError, "timed out"):
            navigation.navigate_from_seller_home_to_ongoing(
                "store-two", navigation_timeout=1, execute_script_fn=execute,
                navigate_page_fn=Mock(), ensure_ongoing_fn=ongoing,
            )
        ongoing.assert_not_called()


class TransportAndReadinessTests(OfflineNavigationTestCase):
    def test_scheduled_navigation_uses_source_assign_not_visit_page(self) -> None:
        self.execute.side_effect = None
        self.execute.return_value = {"ok": True, "scheduled": True}
        navigation.schedule_page_navigation("store-two", MIGRATED_HREF)
        self.execute.assert_called_once()
        self.assertIn("window.setTimeout", self.execute.call_args.args[1])
        self.assertIn("window.location.assign", self.execute.call_args.args[1])
        self.assertNotIn("visit_page", self.execute.call_args.args[1])
        self.assertEqual(self.execute.call_args.kwargs, {"timeout": 30})

    def test_scheduled_navigation_rejects_unproven_url_before_transport(self) -> None:
        with self.assertRaises(ValueError):
            navigation.schedule_page_navigation("store-two", "https://example.invalid/")
        self.execute.assert_not_called()

    def test_readiness_polls_with_target_existing_fifteen_second_probe_timeout(self) -> None:
        self.execute.side_effect = [
            {"href": MIGRATED_HREF, "ready": False, "ready_state": "loading"},
            {"href": MIGRATED_HREF, "ready": True, "ready_state": "complete"},
        ]
        result = navigation.wait_for_target_list_ready("store-two")
        self.assertTrue(result["ready"])
        self.assertEqual(self.execute.call_count, 2)
        for arguments in self.execute.call_args_list:
            self.assertEqual(arguments.kwargs, {"timeout": 15})

    def test_readiness_on_wrong_url_cannot_count_as_success(self) -> None:
        self.execute.side_effect = None
        self.execute.return_value = {"href": SELLER_HREF, "ready": True}
        with patch.object(navigation.time, "monotonic", side_effect=[0.0, 0.1, 2.0]), self.assertRaisesRegex(RuntimeError, "timed out"):
            navigation.wait_for_target_list_ready("store-two", timeout=1)


if __name__ == "__main__":
    unittest.main()
