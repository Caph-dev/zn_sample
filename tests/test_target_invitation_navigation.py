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
from lib import target_invitation_dom as invitations  # noqa: E402

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


class RecoveryClock:
    def __init__(self):
        self.elapsed = 0.0

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


class RecoveryTransport:
    """Offline state transitions, not a stubbed final readiness or submission."""
    def __init__(self, *, seller=False, persistent=False, can_retry=True, shell_delay=0, shop_id="shop-two"):
        self.shop_id = shop_id
        self.href = SELLER_HREF if seller else MIGRATED_HREF.replace("shop-two", shop_id)
        self.error = True
        self.persistent = persistent
        self.can_retry = can_retry
        self.shell_delay = shell_delay
        self.events = []
        self.retry_count = 0
        self.after_retry_href = None
        self.options = []
        self.loading = False

    def execute(self, store_id, script, **options):
        self.options.append(options)
        if script == navigation.INSPECT_NAVIGATION_PAGE_JS:
            self.events.append("identity")
            return {"ok": True, "href": self.href, "page_type": "seller-center" if self.href == SELLER_HREF else "target-invitation",
                    "shop_id": self.shop_id, "shop_region": "US"}
        if "const expectedContext =" in script:
            context = invitations._shop_context(self.href)
            if context != (navigation.AFFILIATE_CENTER_HOST, self.shop_id, "US"):
                return {"ok": False, "reason": "list-context-changed"}
        if "count:rows.length" in script:
            from tests.test_target_invitation_dom import make_page
            self.events.append("arrival")
            return make_page(href=self.href)
        if "shell_ready" in script:
            self.events.append("shell")
            self.shell_delay -= 1
            return {"ok": True, "href": self.href, "shell_ready": self.shell_delay < 0}
        if "has_pagination:hasPagination" in script:
            self.events.append("ready")
            return {"ok": True, "href": self.href, "ready": not self.error and not self.loading, "ongoing": True}
        if "const ongoingTab =" in script:
            self.events.append("ongoing")
            return {"ok": True, "already": True, "href": self.href}
        if "can_retry:!!retry" in script:
            self.events.append("error")
            return {"ok": True, "href": self.href, "error": self.error, "can_retry": self.can_retry}
        if "retry.click()" in script:
            self.events.append("retry")
            self.retry_count += 1
            self.error = self.persistent
            if self.after_retry_href:
                self.href = self.after_retry_href
            return {"ok": True, "clicked": True}
        if "const want = 100;" in script:
            self.events.append("size")
            return {"ok": True, "already": True, "href": self.href}
        if "const lastButton =" in script:
            self.events.append("last")
            return {"ok": True, "already": True}
        raise AssertionError("Unexpected offline script: " + script[:100])

    def navigate(self, store_id, url):
        self.events.append("assign")
        self.href = MIGRATED_HREF.replace("shop-two", self.shop_id)
        return {"ok": True}


class ErrorRecoveryFlowTests(unittest.TestCase):
    def run_recovery(self, transport, **options):
        clock = RecoveryClock()
        self.clock = clock
        with patch.object(navigation.time, "monotonic", clock.monotonic), \
                patch.object(navigation.time, "sleep", clock.sleep), \
                patch("lib.zclaw.zclaw_invoke", side_effect=AssertionError("No live CLI")), \
                patch.object(invitations, "zclaw_exec", transport.execute):
            result = navigation.navigate_from_seller_home_to_ongoing(
                "store-two", execute_script_fn=transport.execute,
                navigate_page_fn=transport.navigate, **options,
            )
        return result, clock

    def test_recoverable_error_reaches_retry_before_ready(self):
        transport = RecoveryTransport()
        result, _ = self.run_recovery(transport)
        self.assertTrue(result["ok"])
        self.assertEqual(transport.retry_count, 1)
        self.assertNotIn("assign", transport.events)
        self.assertLess(transport.events.index("ongoing"), transport.events.index("retry"))
        self.assertLess(transport.events.index("retry"), transport.events.index("ready"))

    def test_recoverable_error_after_seller_navigation(self):
        transport = RecoveryTransport(seller=True)
        result, _ = self.run_recovery(transport)
        self.assertTrue(result["ok"])
        self.assertEqual(transport.events.count("assign"), 1)
        self.assertEqual(transport.retry_count, 1)

    def test_no_retry_control_fails_before_final_readiness(self):
        transport = RecoveryTransport(can_retry=False)
        with self.assertRaisesRegex(RuntimeError, "not recovered"):
            self.run_recovery(transport)
        self.assertEqual(transport.retry_count, 0)
        self.assertNotIn("ready", transport.events)

    def test_persistent_error_stops_at_three_retries_without_outer_recovery(self):
        transport = RecoveryTransport(persistent=True)
        with self.assertRaisesRegex(RuntimeError, "not recovered"):
            self.run_recovery(transport)
        self.assertEqual(transport.retry_count, 3)
        self.assertEqual(transport.events.count("ongoing"), 1)
        self.assertNotIn("size", transport.events)
        self.assertNotIn("assign", transport.events)

    def test_retry_context_changes_stop_without_more_clicks(self):
        for changed_href in (MIGRATED_HREF.replace("shop-two", "other-shop"),
                             MIGRATED_HREF.replace("shop_region=US", "shop_region=GB"),
                             "https://affiliate.tiktokshopglobalselling.com/login"):
            with self.subTest(href=changed_href):
                transport = RecoveryTransport()
                transport.after_retry_href = changed_href
                with self.assertRaisesRegex(RuntimeError, "context-changed"):
                    self.run_recovery(transport)
                self.assertEqual(transport.retry_count, 1)
                self.assertNotIn("size", transport.events)
                self.assertNotIn("last", transport.events)

    def test_delayed_shell_is_read_only_until_tab_is_mounted(self):
        transport = RecoveryTransport(shell_delay=3)
        _, clock = self.run_recovery(transport)
        self.assertEqual(transport.events.count("shell"), 4)
        self.assertEqual(transport.events[:5], ["identity", "shell", "shell", "shell", "shell"])
        self.assertLess(clock.elapsed, 90)

    def test_normal_list_never_clicks_retry(self):
        transport = RecoveryTransport()
        transport.error = False
        self.run_recovery(transport)
        self.assertEqual(transport.retry_count, 0)
        self.assertLess(transport.events.index("ready"), transport.events.index("size"))
        self.assertLess(transport.events.index("size"), transport.events.index("last"))
        self.assertEqual(transport.events[-1], "ready")

    def test_shared_deadline_is_not_extended_by_retry_sleeps(self):
        transport = RecoveryTransport(persistent=True)
        with self.assertRaisesRegex(RuntimeError, "deadline expired"):
            self.run_recovery(transport, navigation_timeout=4)
        self.assertEqual(self.clock.elapsed, 4)
        self.assertEqual(transport.retry_count, 2)
        self.assertTrue(all(options["deadline"] == 4 for options in transport.options))
        self.assertTrue(all(options["timeout"] == 15 for options in transport.options))

    def test_loading_after_retry_does_not_count_as_empty_ready(self):
        transport = RecoveryTransport()
        transport.loading = True
        with self.assertRaisesRegex(RuntimeError, "readiness timed out"):
            self.run_recovery(transport, navigation_timeout=6)
        self.assertEqual(transport.retry_count, 1)
        self.assertNotIn("size", transport.events)
        self.assertLessEqual(self.clock.elapsed, 6)

    def test_legacy_destination_recovers_without_new_navigation(self):
        transport = RecoveryTransport()
        transport.href = LEGACY_HREF
        result, _ = self.run_recovery(transport)
        self.assertTrue(result["already"])
        self.assertEqual(result["destination"]["href"], LEGACY_HREF)
        self.assertNotIn("assign", transport.events)
        self.assertEqual(transport.retry_count, 1)

    def test_new_route_without_shop_id_must_arrive_with_context_before_recovery(self):
        transport = RecoveryTransport()
        transport.href = MIGRATED_HREF.replace("&shop_id=shop-two", "")
        result, _ = self.run_recovery(transport)
        self.assertFalse(result["already"])
        self.assertEqual(transport.events[:3], ["identity", "assign", "identity"])
        self.assertEqual(transport.retry_count, 1)
        self.assertEqual(result["destination"]["shop_id"], "shop-two")

    def test_shell_timeout_has_no_recovery_side_effects(self):
        transport = RecoveryTransport(shell_delay=100)
        with self.assertRaisesRegex(RuntimeError, "shell readiness timed out"):
            self.run_recovery(transport, navigation_timeout=5)
        self.assertEqual(transport.retry_count, 0)
        self.assertNotIn("ongoing", transport.events)
        self.assertNotIn("size", transport.events)
        self.assertLessEqual(self.clock.elapsed, 5)

    def test_transient_readonly_probes_retry_within_shared_deadline(self):
        import subprocess
        for stage in ("shell_ready", "has_pagination:hasPagination", "can_retry:!!retry"):
            for transient in (TimeoutError("Synthetic timeout"), subprocess.TimeoutExpired("probe", 15),
                              RuntimeError("Bridge network unavailable")):
                with self.subTest(stage=stage, transient=type(transient).__name__):
                    transport = RecoveryTransport()
                    original_execute = transport.execute
                    attempts = []
                    def intermittent(store_id, source, **options):
                        if stage in source:
                            attempts.append(options)
                            if len(attempts) == 1:
                                raise transient
                        return original_execute(store_id, source, **options)
                    transport.execute = intermittent
                    result, clock = self.run_recovery(transport)
                    self.assertTrue(result["ok"])
                    self.assertGreaterEqual(len(attempts), 2)
                    self.assertTrue(all(options["deadline"] == 90 for options in attempts))
                    self.assertLess(clock.elapsed, 90)
                    self.assertEqual(transport.retry_count, 1)

    def test_transient_sideeffect_response_loss_is_not_replayed(self):
        for stage in ("retry.click()", "const ongoingTab =", "const want = 100;", "const lastButton ="):
            with self.subTest(stage=stage):
                transport = RecoveryTransport()
                original_execute = transport.execute
                attempts = []
                def lost_response(store_id, source, **options):
                    if stage in source and "shell_ready" not in source and "has_pagination:hasPagination" not in source:
                        attempts.append(options)
                        raise TimeoutError("Synthetic lost sideeffect response")
                    return original_execute(store_id, source, **options)
                transport.execute = lost_response
                with self.assertRaises(TimeoutError):
                    self.run_recovery(transport)
                self.assertEqual(len(attempts), 1)

    def test_transient_read_exhaustion_keeps_original_deadline(self):
        for stage in ("shell_ready", "has_pagination:hasPagination", "can_retry:!!retry", "count:rows.length"):
            with self.subTest(stage=stage):
                transport = RecoveryTransport()
                transport.error = False
                original_execute = transport.execute
                attempts = []
                def always_timeout(store_id, source, **options):
                    if stage in source:
                        attempts.append(options)
                        raise TimeoutError("Synthetic transient timeout")
                    return original_execute(store_id, source, **options)
                transport.execute = always_timeout
                with self.assertRaisesRegex(RuntimeError, "deadline expired"):
                    self.run_recovery(transport, navigation_timeout=4)
                self.assertGreater(len(attempts), 1)
                self.assertLessEqual(self.clock.elapsed, 4)
                self.assertTrue(all(options["deadline"] == 4 for options in attempts))
                self.assertLessEqual(transport.events.count("last"), 1)

    def test_invalid_read_states_and_nontransient_errors_fail_immediately(self):
        for stage in ("shell_ready", "has_pagination:hasPagination", "can_retry:!!retry", "count:rows.length"):
            for bad_state in (None, {"ok": False, "reason": "list-context-changed"}, {"ok": True},
                              RuntimeError("Synthetic unknown failure")):
                with self.subTest(stage=stage, bad_state=bad_state):
                    transport = RecoveryTransport()
                    transport.error = False
                    original_execute = transport.execute
                    attempts = []
                    def reject_read(store_id, source, **options):
                        if stage in source:
                            attempts.append(options)
                            if isinstance(bad_state, Exception):
                                raise bad_state
                            return bad_state
                        return original_execute(store_id, source, **options)
                    transport.execute = reject_read
                    with self.assertRaises(RuntimeError):
                        self.run_recovery(transport)
                    self.assertEqual(len(attempts), 1)
                    self.assertLess(self.clock.elapsed, 90)

    def test_last_page_receipt_requires_observed_complete_destination(self):
        from tests.test_target_invitation_dom import make_covered_page, make_page
        for mode in ("delayed", "stalled", "invalid-total", "partial", "empty", "changed"):
            with self.subTest(mode=mode):
                transport = RecoveryTransport()
                transport.error = False
                original_execute = transport.execute
                observations = []
                def page_transition(store_id, source, **options):
                    if "count:rows.length" in source:
                        observations.append(options)
                        if mode == "empty":
                            return make_page(rows=[], href=transport.href, has_empty_state=True)
                        page_number = 1 if mode == "stalled" or (mode == "delayed" and len(observations) == 1) else 2
                        page = make_covered_page(page_number, 200, href=transport.href)
                        if mode == "invalid-total":
                            page["total"] = "unknown"
                        if mode == "partial":
                            page["rows"] = page["rows"][:50]
                        if mode == "changed":
                            page["href"] = transport.href.replace("shop-two", "other-shop")
                        return page
                    return original_execute(store_id, source, **options)
                transport.execute = page_transition
                if mode in {"delayed", "empty"}:
                    self.run_recovery(transport, navigation_timeout=5)
                    self.assertEqual(len(observations), 2 if mode == "delayed" else 1)
                else:
                    with self.assertRaises(RuntimeError):
                        self.run_recovery(transport, navigation_timeout=5)
                    self.assertLessEqual(self.clock.elapsed, 5)
                self.assertEqual(transport.events.count("last"), 1)
                self.assertTrue(all(options["deadline"] == 5 for options in observations))

    def test_missing_last_control_accepts_only_verified_genuine_empty_list(self):
        from tests.test_target_invitation_dom import make_page
        for empty in (True, False):
            with self.subTest(empty=empty):
                transport = RecoveryTransport()
                transport.error = False
                original_execute = transport.execute
                attempted_actions = []
                def missing_control(store_id, source, **options):
                    if "const lastButton =" in source:
                        attempted_actions.append(source)
                        return {"ok": False, "reason": "last-page-control-not-found"}
                    if "count:rows.length" in source:
                        return make_page(rows=[] if empty else None, has_empty_state=empty, href=transport.href)
                    return original_execute(store_id, source, **options)
                transport.execute = missing_control
                if empty:
                    self.assertTrue(self.run_recovery(transport)[0]["ok"])
                else:
                    with self.assertRaisesRegex(RuntimeError, "without verified empty"):
                        self.run_recovery(transport)
                self.assertEqual(len(attempted_actions), 1)


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
                     MIGRATED_HREF + "&shop_id=other", MIGRATED_HREF + "&shop_region=GB",
                     MIGRATED_HREF + "&shop_id=", MIGRATED_HREF + "&shop_region="):
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
        identity_states = iter(states)
        def transport(store_id, script, **options):
            if script == navigation.INSPECT_NAVIGATION_PAGE_JS:
                return {"ok": True, **next(identity_states)}
            if "can_retry:!!retry" in script:
                return {"ok": True, "error": False, "can_retry": False, "href": MIGRATED_HREF}
            if "const want = 100;" in script or "const lastButton =" in script:
                return {"ok": True, "already": True}
            if "count:rows.length" in script:
                from tests.test_target_invitation_dom import make_page
                return make_page(href=states[-1]["href"])
            raise AssertionError("Unexpected transport call")
        execute = Mock(side_effect=transport)
        navigate = Mock(return_value={"ok": True})
        ongoing = Mock(return_value={"ok": True, "already": True})
        ready = Mock(return_value={"ready": True, "href": MIGRATED_HREF} if readiness is None else readiness)
        shell = Mock(return_value={"ok": True, "href": states[-1]["href"], "shell_ready": True})
        result = navigation.navigate_from_seller_home_to_ongoing(
            "store-two", execute_script_fn=execute, navigate_page_fn=navigate,
            ensure_ongoing_fn=ongoing, wait_ready_fn=ready, wait_shell_fn=shell, **fields,
        )
        self.execute.assert_not_called()
        return result, execute, navigate, ongoing, ready

    def test_already_target_page_skips_location_assign(self) -> None:
        result, execute, navigate, ongoing, ready = self.run_flow([DESTINATION_STATE])
        self.assertTrue(result["already"])
        self.assertEqual(result["destination"]["shop_id"], "shop-two")
        self.assertEqual(sum(call.args[1] == navigation.INSPECT_NAVIGATION_PAGE_JS
                             for call in execute.call_args_list), 1)
        navigate.assert_not_called()
        ongoing.assert_called_once()
        self.assertEqual(ready.call_count, 2)

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
                "store-two", execute_script_fn=Mock(side_effect=lambda store_id, script, **options:
                    DESTINATION_STATE if script == navigation.INSPECT_NAVIGATION_PAGE_JS else
                    {"ok": True, "href": MIGRATED_HREF, "error": False, "can_retry": False}),
                navigate_page_fn=navigate, ensure_ongoing_fn=ongoing,
                wait_ready_fn=Mock(return_value={"ready": False}),
                wait_shell_fn=Mock(return_value={"href": MIGRATED_HREF, "shell_ready": True}),
            )
        navigate.assert_not_called()
        ongoing.assert_called_once()

    def test_navigation_timeout_never_selects_tab_or_restarts_store(self) -> None:
        execute = Mock(side_effect=[{"ok": True, "href": SELLER_HREF, "page_type": "seller-center"}])
        ongoing = Mock()
        clock = RecoveryClock()
        with patch.object(navigation.time, "monotonic", clock.monotonic), \
                patch.object(navigation.time, "sleep", clock.sleep), self.assertRaisesRegex(RuntimeError, "timed out"):
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
            {"ok": True, "href": MIGRATED_HREF, "ready": False, "ready_state": "loading"},
            {"ok": True, "href": MIGRATED_HREF, "ready": True, "ready_state": "complete"},
        ]
        result = navigation.wait_for_target_list_ready("store-two")
        self.assertTrue(result["ready"])
        self.assertEqual(self.execute.call_count, 2)
        for arguments in self.execute.call_args_list:
            self.assertEqual(arguments.kwargs["timeout"], 15)
            self.assertEqual(arguments.kwargs["retries"], 0)
            self.assertIn("deadline", arguments.kwargs)

    def test_readiness_on_wrong_url_cannot_count_as_success(self) -> None:
        self.execute.side_effect = None
        self.execute.return_value = {"ok": True, "href": SELLER_HREF, "ready": True}
        clock = RecoveryClock()
        with patch.object(navigation.time, "monotonic", clock.monotonic), \
                patch.object(navigation.time, "sleep", clock.sleep), self.assertRaisesRegex(RuntimeError, "Not at target"):
            navigation.wait_for_target_list_ready("store-two", timeout=1)


if __name__ == "__main__":
    unittest.main()
