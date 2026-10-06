"""Offline migrations of source date/scrape tests plus fail-closed write checks."""
from __future__ import annotations

import json
import sys
import unittest
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib import target_invitation_dom as invitations  # noqa: E402

TARGET_HREF = (
    "https://affiliate.tiktokshopglobalselling.com/affiliate/collaboration/target-invitation"
    "?shop_region=US&shop_id=shop-two&tab=1"
)
CUTOFF = date(2026, 6, 5)
RESTORED = {"nav": {"ok": True}, "paging": {"ok": True}}


def make_row(invitation_id: str = "old", modified: str = "2025/11/27", **fields) -> dict:
    return {"invitation_id": invitation_id, "name": f"Plan {invitation_id}", "last_modified": modified,
            "invited_count": "10", "accepted_count": "7", "promoted_count": "3", **fields}


def make_page(page_number: int = 1, rows: list[dict] | None = None, **fields) -> dict:
    page_rows = [make_row()] if rows is None else rows
    total = (page_number - 1) * invitations.PAGE_SIZE + len(page_rows) if page_rows else 0
    return {"ok": True, "href": TARGET_HREF, "ready_state": "complete", "ongoing": True,
            "rows": page_rows, "page": str(page_number), "total": str(total),
            "page_size": "100/页", "list_error": False, "has_empty_state": False,
            "next_control_present": True, "next_disabled": True,
            "previous_control_present": True, "previous_disabled": page_number == 1, **fields}


def make_covered_page(page_number: int, total: int, leading_rows: list[dict] | None = None,
                      **fields) -> dict:
    expected_count = min(invitations.PAGE_SIZE, total - (page_number - 1) * invitations.PAGE_SIZE)
    page_rows = list(leading_rows or [])
    page_rows.extend(make_row(f"page-{page_number}-row-{index}")
                     for index in range(len(page_rows), expected_count))
    return make_page(page_number, page_rows, total=str(total),
                     next_disabled=page_number == (total + invitations.PAGE_SIZE - 1) // invitations.PAGE_SIZE,
                     **fields)


class OfflineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.sleep = self.context.enter_context(patch.object(invitations.time, "sleep"))
        self.execute = self.context.enter_context(patch.object(
            invitations, "zclaw_exec", side_effect=AssertionError("No live ZClaw calls allowed"),
        ))
        self.context.enter_context(patch("lib.zclaw.zclaw_invoke", side_effect=AssertionError("No CLI allowed")))


class DateSemanticsTests(OfflineTestCase):
    def test_supported_source_dates_and_ambiguous_dates(self) -> None:
        for text in ("2025/11/27", "2025-11-27", "2025.11.27", "Nov 27, 2025", "27 November 2025",
                     "27 Nov 2025", "NOVEMBER 27, 2025", " 2025/11/27 "):
            with self.subTest(text=text):
                self.assertEqual(invitations.parse_ui_date(text), date(2025, 11, 27))
        for text in ("", "unknown", "05/06/2026", "2025/02/30", "Feb 30, 2025", "昨天", "乱码�",
                     "2025/11/27 00:00", "2025/-1/20", "Nov -1, 2025"):
            with self.subTest(text=text):
                self.assertIsNone(invitations.parse_ui_date(text))

    def test_two_and_four_natural_months_end_of_month_and_leap_year(self) -> None:
        cases = (
            (date(2026, 10, 5), 2, date(2026, 8, 5)),
            (date(2026, 10, 5), 4, date(2026, 6, 5)),
            (date(2026, 4, 30), 2, date(2026, 2, 28)),
            (date(2024, 4, 30), 2, date(2024, 2, 29)),
            (date(2024, 6, 30), 4, date(2024, 2, 29)),
            (date(2026, 1, 31), 2, date(2025, 11, 30)),
            (date(2026, 1, 31), 4, date(2025, 9, 30)),
            (date(2100, 6, 30), 4, date(2100, 2, 28)),
        )
        for run_date, months, expected in cases:
            with self.subTest(run_date=run_date, months=months):
                self.assertEqual(invitations.months_ago(run_date, months), expected)
        for invalid in (True, False, 2.0, "2", 0, 1, 3, 6, -2):
            with self.subTest(months=invalid), self.assertRaises(ValueError):
                invitations.months_ago(date(2026, 10, 5), invalid)

    def test_source_age_only_candidate_ids_and_exclusive_boundary(self) -> None:
        rows = [make_row("old", "2026/06/04"), make_row("boundary", "2026/06/05"),
                make_row("two-month-only", "2026/07/01"), make_row("recent", "2026/08/05"),
                make_row("", "2026/01/01", row_key="12345"), make_row("unknown", "unknown")]
        # Golden IDs from source evaluate_row(no-zero-check) plus its missing-ID exclusion.
        expected_by_months = {4: ["old"], 2: ["old", "boundary", "two-month-only"]}
        for months, expected_ids in expected_by_months.items():
            cutoff = invitations.months_ago(date(2026, 10, 5), months)
            self.assertEqual([row["invitation_id"] for row in rows
                              if invitations.evaluate_invitation(row, cutoff=cutoff)[0]], expected_ids)
        for counts in ((7, 3), (0, 0), ("unknown", None)):
            self.assertTrue(invitations.evaluate_invitation(
                make_row(accepted_count=counts[0], promoted_count=counts[1]), cutoff=CUTOFF,
            )[0])
        self.execute.assert_not_called()

    def test_date_decision_migrated_from_source(self) -> None:
        self.assertEqual(invitations.scrape_date_decision("2026/08/01", None), "keep")
        self.assertEqual(invitations.scrape_date_decision("", CUTOFF), "unparsed")
        self.assertEqual(invitations.scrape_date_decision("昨天", CUTOFF), "unparsed")
        self.assertEqual(invitations.scrape_date_decision("2026/06/05", CUTOFF), "too_new")
        self.assertEqual(invitations.scrape_date_decision("2026-08-01", CUTOFF), "too_new")
        self.assertEqual(invitations.scrape_date_decision("2026/06/04", CUTOFF), "keep")

    def test_scan_accumulation_preserves_new_rows_and_missing_id_diagnostics(self) -> None:
        seen: set[str] = set()
        rows = [make_row("old"), make_row("boundary", "2026/06/05"), make_row("", row_key="name|date|10")]
        added, missing_id, too_new = invitations.accumulate_scrape_page(rows, page_no=43, seen=seen, cutoff=CUTOFF)
        self.assertEqual([row["invitation_id"] for row in added], ["old", "boundary", ""])
        self.assertEqual(missing_id, 1)
        self.assertTrue(too_new)
        self.assertEqual([row["page"] for row in added], [43, 43, 43])
        self.assertNotIn("page", rows[0])
        self.assertEqual(invitations.accumulate_scrape_page(rows, page_no=43, seen=seen, cutoff=CUTOFF)[0], [])

    def test_frozen_cutoff_is_required_and_datetime_is_not_calendar_date(self) -> None:
        for invalid in (None, "2026-06-05", datetime(2026, 6, 5)):
            with self.subTest(cutoff=invalid), self.assertRaises(ValueError):
                invitations.scan_older_invitations("store-two", cutoff=invalid)
        self.execute.assert_not_called()


class ExtractionTests(OfflineTestCase):
    def test_rereads_incomplete_dates_on_same_page(self) -> None:
        incomplete = make_page(61, [make_row(modified="")])
        ready = make_page(61)
        self.execute.side_effect = [incomplete, ready]
        self.assertEqual(invitations.extract_page("store-two"), ready)
        self.assertEqual(self.execute.call_count, 2)
        self.sleep.assert_called_once_with(1.0)
        for arguments in self.execute.call_args_list:
            self.assertEqual(arguments.args, ("store-two", invitations.EXTRACT_JS))

    def test_preserves_bad_dates_after_three_reads(self) -> None:
        incomplete = make_page(61, [make_row(modified="unknown")])
        self.execute.side_effect = None
        self.execute.return_value = incomplete
        self.assertEqual(invitations.extract_page("store-two"), incomplete)
        self.assertEqual(self.execute.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_body_missing_or_malformed_rows_cannot_be_empty_success(self) -> None:
        for response in ({"ok": False, "reason": "no-document-body", "rows": []},
                         {"rows": None}, {"rows": ["bad"]}, "bad"):
            self.execute.side_effect = None
            self.execute.return_value = response
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                invitations.extract_page("store-two")


class ScanCompletenessTests(OfflineTestCase):
    def run_scan(self, pages: list[dict], *, navigation: dict | None = None):
        restore = self.context.enter_context(patch.object(
            invitations, "restore_ongoing_list", return_value=RESTORED if navigation is None else {"nav": navigation},
        ))
        extract = self.context.enter_context(patch.object(invitations, "extract_page", side_effect=pages))
        self.execute.side_effect = None
        self.execute.return_value = {"ok": True}
        result = invitations.scan_older_invitations("store-two", cutoff=CUTOFF)
        restore.assert_called_once_with("store-two")
        for arguments in self.execute.call_args_list:
            self.assertIn(arguments.args[1], (invitations.PREPARE_LIST_JS, invitations.CLICK_PREV_JS))
        return result, extract

    def test_unknown_total_partial_pages_are_incomplete(self) -> None:
        pages = [
            make_page(2, [make_row(f"old-{index}") for index in range(20)], total=""),
            make_page(1, [make_row(f"boundary-{index}", "2026/06/05") for index in range(20)],
                      total="", next_disabled=False),
        ]
        result, _ = self.run_scan(pages)
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "page-total-unverified")
        self.assertEqual(result["pages_scanned"], 1)
        self.assertEqual([row["invitation_id"] for row in result["rows"]],
                         [f"old-{index}" for index in range(20)])

    def test_reverse_date_boundary_is_complete_and_keeps_scanned_recent_rows(self) -> None:
        result, _ = self.run_scan([
            make_covered_page(3, 201, [make_row()]),
            make_covered_page(2, 201, [make_row("boundary", "2026/06/05")]),
        ])
        self.assertEqual(result["stop_reason"], "date-boundary")
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(len(result["rows"]), 101)
        self.assertEqual([row["invitation_id"] for row in result["rows"][:2]], ["old", "boundary"])

    def test_complete_reverse_scan_reaches_first_page_with_consistent_total(self) -> None:
        result, _ = self.run_scan([make_covered_page(page_number, 201) for page_number in (3, 2, 1)])
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "first-page")
        self.assertEqual(result["pages_scanned"], 3)
        self.assertEqual(len(result["rows"]), 201)
        self.assertEqual(len({row["invitation_id"] for row in result["rows"]}), 201)

    def test_verified_first_page_is_complete(self) -> None:
        result, _ = self.run_scan([make_page()])
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "first-page")

    def test_first_page_label_without_disabled_previous_is_incomplete(self) -> None:
        for fields in ({"previous_control_present": False}, {"previous_disabled": False}):
            with self.subTest(fields=fields):
                result, _ = self.run_scan([make_page(**fields)])
                self.assertFalse(result["scan_complete"])
                self.assertEqual(result["stop_reason"], "first-page-unverified")

    def test_unverified_last_page_does_not_allow_recent_page_early_stop(self) -> None:
        result, _ = self.run_scan([make_page(1, [make_row("new", "2026/10/01")], next_control_present=False)])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "last-page-unverified")

    def test_last_page_navigation_failure_never_scans_as_complete(self) -> None:
        result, _ = self.run_scan([make_page()], navigation={"ok": False, "reason": "no-last"})
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "last-page-navigation-failed")

    def test_confirmed_empty_list_does_not_require_nonexistent_pagination_controls(self) -> None:
        result, _ = self.run_scan([make_page(rows=[], has_empty_state=True, page="", page_size="")],
                                  navigation={"ok": False, "reason": "no-last"})
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "empty-list")

    def test_unknown_dates_block_even_a_page_containing_the_date_boundary(self) -> None:
        result, _ = self.run_scan([make_page(3, [make_row(), make_row("bad", "乱码�"), make_row("new", "2026/10/01")])])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "unparsed-last-modified")
        self.assertEqual(result["rows"][1]["last_modified"], "乱码�")

    def test_missing_id_is_preserved_but_not_a_candidate(self) -> None:
        result, _ = self.run_scan([make_page(rows=[make_row("", row_key="old")])])
        self.assertTrue(result["scan_complete"])
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(invitations.evaluate_invitation(result["rows"][0], cutoff=CUTOFF),
                         (False, "missing-invitation-id"))

    def test_true_empty_state_is_complete_but_loading_error_or_unproven_empty_is_not(self) -> None:
        result, _ = self.run_scan([make_page(rows=[], has_empty_state=True)])
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "empty-list")
        for fields in ({"has_empty_state": False}, {"ready_state": "loading", "has_empty_state": True},
                       {"list_error": True, "has_empty_state": True}, {"ongoing": False, "has_empty_state": True}):
            with self.subTest(fields=fields):
                result, _ = self.run_scan([make_page(rows=[], **fields)])
                self.assertFalse(result["scan_complete"])

    def test_fifty_page_limit_preserves_rows_and_never_clicks_the_fifty_first_page(self) -> None:
        pages = [make_covered_page(page_number, 5901) for page_number in range(60, 10, -1)]
        result, _ = self.run_scan(pages)
        self.assertEqual(result["pages_scanned"], 50)
        self.assertEqual(len(result["rows"]), 4901)
        self.assertEqual(result["stop_reason"], "max-pages")
        self.assertFalse(result["scan_complete"])
        self.assertEqual(sum(arguments.args[1] == invitations.CLICK_PREV_JS
                             for arguments in self.execute.call_args_list), 49)

    def test_previous_page_failure_does_not_mean_first_page(self) -> None:
        self.context.enter_context(patch.object(invitations, "restore_ongoing_list", return_value=RESTORED))
        self.context.enter_context(patch.object(invitations, "extract_page", return_value=make_page(3)))
        self.execute.side_effect = [{"ok": True}, {"ok": False, "reason": "no-prev"}]
        result = invitations.scan_older_invitations("store-two", cutoff=CUTOFF)
        self.assertEqual(result["stop_reason"], "previous-page-failed")
        self.assertFalse(result["scan_complete"])

    def test_page_stall_duplicate_content_wrong_sequence_or_shop_change_is_incomplete(self) -> None:
        repeated_rows = [make_row(f"repeated-{index}") for index in range(100)]
        cases = (
            ([make_page(3)] * 13, "pagination-stalled"),
            ([make_page(3, repeated_rows, total="300"),
              make_page(2, repeated_rows, total="300", next_disabled=False)], "repeated-page"),
            ([make_page(3), make_page(1, [make_row("other")])], "page-sequence-changed"),
            ([make_page(3), make_covered_page(2, 201, href=TARGET_HREF.replace("shop-two", "shop-other"))],
             "list-context-changed"),
        )
        for pages, expected in cases:
            with self.subTest(expected=expected):
                result, _ = self.run_scan(pages)
                self.assertFalse(result["scan_complete"])
                self.assertEqual(result["stop_reason"], expected)

    def test_unknown_or_invalid_totals_never_allow_boundary_or_first_page_completion(self) -> None:
        for total in ("", "Total: 20", None, "-1", "20.0", "\u00b2"):
            for page_number in (1, 2):
                with self.subTest(total=total, page_number=page_number):
                    rows = [make_row(f"boundary-{index}", "2026/06/05") for index in range(20)]
                    result, _ = self.run_scan([make_page(page_number, rows, total=total)])
                    self.assertFalse(result["scan_complete"])
                    self.assertEqual(result["stop_reason"], "page-total-unverified")
                    self.assertEqual(len(result["rows"]), 20)

    def test_full_page_and_disabled_next_do_not_substitute_for_total_evidence(self) -> None:
        page = make_page(rows=[make_row(f"old-{row_index}") for row_index in range(100)])
        del page["total"]
        result, _ = self.run_scan([page])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "page-total-unverified")
        self.assertEqual(len(result["rows"]), 100)

    def test_zero_out_of_range_or_partial_rows_with_known_total_are_incomplete(self) -> None:
        cases = (
            make_page(total="0"), make_page(total=0), make_page(2, total="1"),
            make_page(2, [make_row(f"old-{index}") for index in range(20)], total="220"),
            make_page(3, [make_row(f"old-{index}") for index in range(20)], total="225"),
            make_page(rows=[make_row("boundary", "2026/06/05")], total="2"),
        )
        for page in cases:
            with self.subTest(page_number=page["page"], total=page["total"]):
                result, _ = self.run_scan([page])
                self.assertFalse(result["scan_complete"])
                self.assertEqual(result["stop_reason"], "page-rows-incomplete")
                self.assertEqual(len(result["rows"]), len(page["rows"]))

    def test_partial_nonlast_page_cannot_be_overridden_by_date_boundary(self) -> None:
        result, _ = self.run_scan([
            make_covered_page(3, 220),
            make_page(2, [make_row(f"boundary-{index}", "2026/06/05") for index in range(20)],
                      total="220", next_disabled=False),
        ])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "page-rows-incomplete")
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(len(result["rows"]), 40)

    def test_partial_virtual_rows_or_wrong_page_size_are_blocked(self) -> None:
        for fields in ({"total": "200"}, {"page_size": "50/页"}, {"page_size": "1000/页"}):
            with self.subTest(fields=fields):
                result, _ = self.run_scan([make_page(**fields)])
                self.assertFalse(result["scan_complete"])

    def test_disabled_next_cannot_override_a_contradicting_observed_total(self) -> None:
        rows = [make_row(str(row_number)) for row_number in range(100)]
        result, _ = self.run_scan([make_page(1, rows, total="200")])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "last-page-unverified")

    def test_empty_state_after_nonempty_page_is_not_global_empty_completion(self) -> None:
        result, _ = self.run_scan([make_page(3), make_page(2, rows=[], has_empty_state=True)])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "unexpected-empty-page")
        self.assertEqual(len(result["rows"]), 1)

    def test_empty_state_with_positive_observed_total_is_unverified(self) -> None:
        result, _ = self.run_scan([make_page(rows=[], total="10", has_empty_state=True)])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "empty-state-unverified")

    def test_extraction_exception_retains_previous_scan_rows(self) -> None:
        result, _ = self.run_scan([make_page(3), RuntimeError("Bridge offline")])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["stop_reason"], "scan-error")
        self.assertEqual(len(result["rows"]), 1)


class RecoveryTests(OfflineTestCase):
    def test_recovery_order_is_ongoing_retry_100_then_last_page(self) -> None:
        self.execute.side_effect = [
            {"ok": True, "clicked": True}, {"ok": True, "already": True},
            {"ok": True, "error": True, "can_retry": True}, {"ok": True}, {"ok": True, "error": False},
            {"ok": True, "set": True}, {"ok": True},
        ]
        result = invitations.restore_ongoing_list("store-two")
        self.assertTrue(result["nav"]["ok"])
        self.assertEqual([arguments.args[1] for arguments in self.execute.call_args_list], [
            invitations.ENSURE_ONGOING_TAB_JS, invitations.ENSURE_ONGOING_TAB_JS,
            invitations.INSPECT_LIST_ERROR_JS, invitations.CLICK_LIST_RETRY_JS, invitations.INSPECT_LIST_ERROR_JS,
            invitations.SET_PAGE_SIZE_JS, invitations.GOTO_LAST_PAGE_JS,
        ])
        self.assertIn("const want = 100;", invitations.SET_PAGE_SIZE_JS)

    def test_ongoing_tab_must_be_verified_after_click(self) -> None:
        self.execute.side_effect = [{"ok": True, "clicked": True}, {"ok": True, "clicked": True}]
        with self.assertRaisesRegex(RuntimeError, "not verified"):
            invitations.ensure_ongoing_tab("store-two")

    def test_page_size_failure_is_not_ignored(self) -> None:
        self.execute.side_effect = [{"ok": True, "already": True}, {"ok": True, "error": False},
                                    {"ok": False}, {"ok": True, "error": False}, {"ok": False}, make_page()]
        with self.assertRaisesRegex(RuntimeError, "100 rows"):
            invitations.restore_ongoing_list("store-two")
        self.assertNotIn(invitations.GOTO_LAST_PAGE_JS, [arguments.args[1] for arguments in self.execute.call_args_list])

    def test_last_page_navigation_has_four_bounded_attempts(self) -> None:
        self.execute.side_effect = [{"ok": True, "already": True}, {"ok": True, "error": False},
                                    {"ok": True, "already": True}, *[{"ok": False}] * 4, make_page()]
        self.assertFalse(invitations.restore_ongoing_list("store-two")["nav"]["ok"])
        self.assertEqual(sum(arguments.args[1] == invitations.GOTO_LAST_PAGE_JS
                             for arguments in self.execute.call_args_list), 4)

    def test_verified_empty_state_can_recover_without_a_page_size_widget(self) -> None:
        self.execute.side_effect = [{"ok": True, "already": True}, {"ok": True, "error": False},
                                    {"ok": False}, {"ok": True, "error": False}, {"ok": False},
                                    make_page(rows=[], has_empty_state=True, page="", page_size="")]
        result = invitations.restore_ongoing_list("store-two")
        self.assertEqual(result["nav"], {"ok": True, "empty": True})
        self.assertNotIn(invitations.GOTO_LAST_PAGE_JS, [arguments.args[1] for arguments in self.execute.call_args_list])

    def test_source_list_recovery_stops_after_three_retry_clicks(self) -> None:
        error_state = {"ok": True, "error": True, "can_retry": True}
        self.execute.side_effect = [error_state, {"ok": True}, error_state, {"ok": True},
                                    error_state, {"ok": True}, error_state]
        with self.assertRaisesRegex(RuntimeError, "not recovered"):
            invitations.recover_list_if_error("store-two")
        self.assertEqual(sum(arguments.args[1] == invitations.CLICK_LIST_RETRY_JS
                             for arguments in self.execute.call_args_list), 3)


class FixedTargetTests(OfflineTestCase):
    def test_locator_returns_only_requested_id_and_never_calls_cancellation(self) -> None:
        self.context.enter_context(patch.object(invitations, "restore_ongoing_list", return_value=RESTORED))
        self.context.enter_context(patch.object(invitations, "extract_page", side_effect=[
            make_covered_page(3, 201, [make_row("unlisted")]),
            make_covered_page(2, 201, [make_row("old"), make_row("another-unlisted")]),
        ]))
        self.execute.side_effect = None
        self.execute.return_value = {"ok": True}
        result = invitations.locate_target_invitation(
            "store-two", invitation_id="old", expected_last_modified="2025/11/27", cutoff=CUTOFF,
        )
        self.assertTrue(result["found"])
        self.assertTrue(result["revalidated"])
        self.assertEqual(result["row"]["invitation_id"], "old")
        self.assertEqual(result["pages_scanned"], 2)
        self.assertNotIn("rows", result)
        for arguments in self.execute.call_args_list:
            self.assertIn(arguments.args[1], (invitations.PREPARE_LIST_JS, invitations.CLICK_PREV_JS))

    def test_locator_shrinks_on_date_change_and_refuses_fallback_key(self) -> None:
        self.context.enter_context(patch.object(invitations, "restore_ongoing_list", return_value=RESTORED))
        extract = self.context.enter_context(patch.object(
            invitations, "extract_page", return_value=make_page(rows=[make_row("old", "2026/10/01")]),
        ))
        self.execute.side_effect = None
        self.execute.return_value = {"ok": True}
        result = invitations.locate_target_invitation(
            "store-two", invitation_id="old", expected_last_modified="2025/11/27", cutoff=CUTOFF,
        )
        self.assertTrue(result["found"])
        self.assertFalse(result["revalidated"])
        self.assertEqual(result["reason"], "last-modified-changed")
        extract.return_value = make_page(rows=[make_row("", row_key="old")])
        result = invitations.locate_target_invitation(
            "store-two", invitation_id="old", expected_last_modified="2025/11/27", cutoff=CUTOFF,
        )
        self.assertFalse(result["found"])
        self.assertEqual(result["reason"], "not-found")

    def test_locator_does_not_infer_absence_from_unverified_first_page(self) -> None:
        self.context.enter_context(patch.object(invitations, "restore_ongoing_list", return_value=RESTORED))
        self.context.enter_context(patch.object(
            invitations, "extract_page", return_value=make_page(rows=[make_row("other")], previous_control_present=False),
        ))
        self.execute.side_effect = None
        self.execute.return_value = {"ok": True}
        result = invitations.locate_target_invitation(
            "store-two", invitation_id="old", expected_last_modified="2025/11/27", cutoff=CUTOFF,
        )
        self.assertEqual(result["reason"], "first-page-unverified")

    def test_locator_does_not_trust_even_a_matching_id_without_complete_page_evidence(self) -> None:
        self.context.enter_context(patch.object(invitations, "restore_ongoing_list", return_value=RESTORED))
        extract = self.context.enter_context(patch.object(invitations, "extract_page"))
        self.execute.side_effect = None
        self.execute.return_value = {"ok": True}
        for total, reason in (("", "page-total-unverified"), ("2", "page-rows-incomplete")):
            with self.subTest(total=total):
                extract.return_value = make_page(total=total)
                result = invitations.locate_target_invitation(
                    "store-two", invitation_id="old", expected_last_modified="2025/11/27", cutoff=CUTOFF,
                )
                self.assertFalse(result["found"])
                self.assertFalse(result["revalidated"])
                self.assertEqual(result["reason"], reason)
        for arguments in self.execute.call_args_list:
            self.assertEqual(arguments.args[1], invitations.PREPARE_LIST_JS)

    def test_invalid_fixed_target_stops_before_any_transport(self) -> None:
        for fields in ({"invitation_id": ""}, {"expected_last_modified": "unknown"},
                       {"expected_last_modified": "2026/06/05"}, {"execute": "true"}):
            arguments = {"invitation_id": "old", "expected_last_modified": "2025/11/27", "cutoff": CUTOFF, **fields}
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                invitations.cancel_invitation_by_id("store-two", **arguments)
        self.execute.assert_not_called()


class CancellationEvidenceTests(OfflineTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.original_readback = invitations._wait_invitation_gone
        self.extract = self.context.enter_context(patch.object(invitations, "extract_page", return_value=make_page()))
        self.restore = self.context.enter_context(patch.object(invitations, "restore_ongoing_list", return_value=RESTORED))
        self.readback = self.context.enter_context(patch.object(invitations, "_wait_invitation_gone", return_value=True))

    def cancel(self, **fields):
        return invitations.cancel_invitation_by_id(
            "store-two", invitation_id="old", expected_last_modified="2025/11/27", cutoff=CUTOFF, **fields,
        )

    def test_default_dry_run_does_not_dispatch_write_or_open_menu(self) -> None:
        result = self.cancel()
        self.assertEqual(result["status"], "dry-run")
        self.assertFalse(result["write_attempted"])
        self.execute.assert_not_called()
        self.restore.assert_not_called()
        self.readback.assert_not_called()

    def test_changed_date_duplicate_id_or_nonongoing_page_never_dispatches_write(self) -> None:
        for page in (make_page(rows=[make_row(modified="2026/10/01")]),
                     make_page(rows=[make_row(), make_row()]), make_page(ongoing=False),
                     make_page(rows=[make_row("", row_key="old")])):
            with self.subTest(page=page):
                self.extract.return_value = page
                result = self.cancel(execute=True)
                self.assertEqual(result["status"], "skipped")
                self.assertFalse(result["write_attempted"])
        self.execute.assert_not_called()

    def test_unknown_total_or_partial_page_never_dispatches_write(self) -> None:
        for total, reason in (("", "page-total-unverified"), ("2", "page-rows-incomplete")):
            with self.subTest(total=total):
                self.extract.return_value = make_page(total=total)
                result = self.cancel(execute=True)
                self.assertEqual(result["status"], "skipped")
                self.assertFalse(result["write_attempted"])
                self.assertEqual(result["reason"], reason)
        self.execute.assert_not_called()
        self.restore.assert_not_called()
        self.readback.assert_not_called()

    def test_confirmed_and_same_page_gone_is_submitted_not_platform_success(self) -> None:
        raw = {"ok": True, "invitation_id": "old", "clickedCancel": True, "confirmed": True, "write_attempted": True}
        self.execute.side_effect = None
        self.execute.return_value = raw
        result = self.cancel(execute=True)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["raw"], raw)
        self.assertTrue(result["confirmed"])
        self.assertTrue(result["gone"])
        self.execute.assert_called_once()
        self.assertEqual(self.execute.call_args.kwargs, {"retries": 0, "retry_timeout_expired": False})
        self.restore.assert_called_once()
        self.sleep.assert_called_once_with(1.2)

    def test_broad_source_ok_missing_confirmation_or_persistent_row_is_uncertain(self) -> None:
        for raw, gone in (({"ok": True}, True),
                          ({"ok": True, "invitation_id": "old", "clickedCancel": True, "confirmed": False}, True),
                          ({"ok": True, "invitation_id": "old", "clickedCancel": True, "confirmed": True}, False),
                          ({"ok": True, "invitation_id": "old", "clickedCancel": True, "confirmed": True}, None)):
            with self.subTest(raw=raw, gone=gone):
                self.execute.side_effect = None
                self.execute.return_value = raw
                self.readback.return_value = gone
                self.assertEqual(self.cancel(execute=True)["status"], "uncertain")

    def test_final_js_guard_rejection_is_definitely_not_written(self) -> None:
        self.execute.side_effect = None
        self.execute.return_value = {"ok": False, "invitation_id": "old", "write_attempted": False,
                                     "reason": "last-modified-changed"}
        result = self.cancel(execute=True)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["write_attempted"])
        self.assertEqual(result["reason"], "last-modified-changed")
        self.readback.assert_not_called()

    def test_write_disconnect_never_retries_and_preserves_uncertain_result(self) -> None:
        self.execute.side_effect = RuntimeError("Bridge disconnected after click")
        result = self.cancel(execute=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["write_attempted"])
        self.execute.assert_called_once()
        self.restore.assert_called_once()
        self.readback.assert_not_called()

    def test_recovery_failure_downgrades_submitted_to_uncertain(self) -> None:
        self.execute.side_effect = None
        self.execute.return_value = {"ok": True, "invitation_id": "old", "clickedCancel": True, "confirmed": True}
        self.restore.side_effect = RuntimeError("List recovery failed")
        result = self.cancel(execute=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["reason"], "postwrite-recovery-failed")
        self.assertTrue(result["confirmed"])
        self.assertTrue(result["gone"])
        self.execute.assert_called_once()

    def test_readback_does_not_infer_gone_after_a_tab_or_page_change(self) -> None:
        for page in (make_page(2, rows=[make_row("other")]),
                     make_page(rows=[make_row("other")], ongoing=False),
                     make_page(rows=[make_row("other")], href=TARGET_HREF.replace("shop-two", "other-shop")),
                     make_page(rows=[make_row("", row_key="other")])):
            with self.subTest(page=page):
                self.extract.return_value = page
                self.assertIsNone(self.original_readback("store-two", "old", make_page()))

    def test_actual_readback_distinguishes_same_page_absence_and_persistent_row(self) -> None:
        self.extract.return_value = make_page(rows=[make_row("other")])
        self.assertTrue(self.original_readback("store-two", "old", make_page()))
        self.extract.return_value = make_page()
        self.assertFalse(self.original_readback("store-two", "old", make_page()))

    def test_readback_does_not_infer_absence_from_unknown_total_or_partial_rows(self) -> None:
        for total in ("", "2"):
            with self.subTest(total=total):
                self.extract.return_value = make_page(rows=[make_row("other")], total=total)
                self.assertIsNone(self.original_readback("store-two", "old", make_page()))

    def test_id_template_markers_are_rendered_once_as_data_not_code(self) -> None:
        invitation_id = 'fixed-%EXPECTED_HREF%-%DO_CANCEL%-"'
        self.extract.return_value = make_page(rows=[make_row(invitation_id)])
        self.execute.side_effect = None
        self.execute.return_value = {"ok": False, "invitation_id": invitation_id, "write_attempted": False,
                                     "reason": "no-arrow"}
        result = invitations.cancel_invitation_by_id(
            "store-two", invitation_id=invitation_id, expected_last_modified="2025/11/27", cutoff=CUTOFF, execute=True,
        )
        self.assertFalse(result["write_attempted"])
        script = self.execute.call_args.args[1]
        self.assertIn("const targetId = " + json.dumps(invitation_id) + ";", script)
        self.assertIn("const doCancel = true;", script)


if __name__ == "__main__":
    unittest.main()
