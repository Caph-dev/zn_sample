"""Offline migrations of source date/scrape tests plus fail-closed write checks."""
from __future__ import annotations

import json
import shutil
import subprocess
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


ASYNC_DOM_HARNESS_JS = r"""
const productionScripts = JSON.parse(process.argv[1]);
const renderMode = process.argv[2];
const settings = JSON.parse(process.argv[3]);
const events = {trigger:0, option:0};
let pageSize = settings.initialPageSize || '50/页', optionMounted = false;
let clockTicks = 0;
Date.now = () => ++clockTicks * 1000;
const scheduleRender = callback => renderMode === 'microtask'
  ? queueMicrotask(callback) : setTimeout(callback, 0);
const element = (text, click = () => {}) => ({
  innerText:text, click, classList:{contains:() => false},
  getAttribute:() => null, hasAttribute:() => false,
  getBoundingClientRect:() => ({width:100, height:20})
});
const trigger = element('', () => {events.trigger++; scheduleRender(() => {
  optionMounted = true;
  if (settings.changedHref) location.href = settings.changedHref;
});});
const option = element('100/页', () => {events.option++; scheduleRender(() => {pageSize = '100/页';});});
globalThis.location = {href:%TARGET_HREF%};
globalThis.document = {
  querySelector:selector => {
    if (selector === '.core-pagination-option .core-select-view-value') return element(pageSize);
    if (selector.startsWith('.core-pagination-option')) return trigger;
    return null;
  },
  querySelectorAll:selector => {
    if (selector.includes('role=option')) return optionMounted
      ? Array(settings.optionCount === undefined ? 1 : settings.optionCount).fill(option) : [];
    return [];
  }
};
(async () => {
  const responses = [];
  const clickCounts = [];
  for (const productionScript of productionScripts) {
    responses.push(JSON.parse(eval(productionScript)));
    clickCounts.push({...events});
    // A transport return yields to both microtasks and zero-delay render timers.
    await new Promise(resolve => setTimeout(resolve, 0));
  }
  process.stdout.write(JSON.stringify({responses, events, pageSize, clickCounts}));
})().catch(error => {console.error(error); process.exitCode = 1;});
"""

CANCELLATION_DOM_HARNESS_JS = r"""
const scripts = JSON.parse(process.argv[1]);
const renderMode = process.argv[2];
const settings = JSON.parse(process.argv[3]);
const events = {arrow:0, cancel:0, confirm:0, keep:0, unrelated:0};
const scheduleRender = callback => renderMode === 'microtask'
  ? queueMicrotask(callback) : setTimeout(callback, 0);
const element = (text, click = () => {}) => ({
  innerText:text, click, getBoundingClientRect:() => ({width:100, height:20})
});
const record = {id:'old', name:'Old plan'};
const modalProperties = {onOk:() => {}, okText:settings.confirmText || 'Cancel',
  cancelText:settings.keepText || 'Keep', title:'End this invitation?'};
let menuMounted = false, dialogMounted = false;
const unrelated = element(modalProperties.okText, () => events.unrelated++);
unrelated.__reactFiberTest = {memoizedProps:modalProperties};
const confirmation = element(modalProperties.okText, () => events.confirm++);
confirmation.__reactFiberTest = {memoizedProps:modalProperties};
const keep = element(modalProperties.cancelText, () => events.keep++);
keep.__reactFiberTest = {memoizedProps:modalProperties};
const menuItem = element('Cancel invitation', () => {
  events.cancel++;
  scheduleRender(() => {menuMounted = false; dialogMounted = Boolean(settings.confirmation);});
});
menuItem.__reactFiberTest = {memoizedProps:{record:{id:settings.menuOwner || 'old'}}};
const arrow = element('', () => {events.arrow++; scheduleRender(() => {menuMounted = true;});});
const cells = [element('Old plan\nLast modified: 2025/11/27'), element('7'), element('3')];
cells[2].querySelectorAll = () => [element('Edit'), arrow];
const tableRow = {__reactFiberTest:{memoizedProps:{record}},
  scrollIntoView:() => {}, querySelectorAll:() => cells};
globalThis.window = globalThis;
globalThis.location = {href:%TARGET_HREF%};
globalThis.document = {querySelectorAll:selector => {
  if (selector === 'table tbody tr') return [tableRow];
  if (selector === '[role=menuitem]') return menuMounted ? [menuItem] : [];
  if (selector === 'button') return [unrelated, arrow, ...(dialogMounted ? [confirmation, keep] : [])];
  return [];
}};
(async () => {
  const responses = [];
  for (const script of scripts) {
    responses.push(JSON.parse(eval(script)));
    await new Promise(resolve => setTimeout(resolve, 0));
  }
  process.stdout.write(JSON.stringify({responses, events, stateCleared:!window.__znSampleCleanupAttempt}));
})().catch(error => {console.error(error); process.exitCode = 1;});
"""


def run_async_dom_scripts(scripts: list[str], render_mode: str, **settings) -> dict:
    node_path = shutil.which("node")
    if node_path is None:
        raise RuntimeError("Node is required for the offline production-JS regression; do not skip it")
    harness = ASYNC_DOM_HARNESS_JS.replace("%TARGET_HREF%", json.dumps(TARGET_HREF))
    completed = subprocess.run(
        [node_path, "-e", harness, json.dumps(scripts), render_mode, json.dumps(settings)],
        shell=False, capture_output=True, text=True, encoding="utf-8", timeout=10, check=True,
    )
    return json.loads(completed.stdout)


class AsyncRenderingTests(OfflineTestCase):
    def test_cancellation_scripts_allow_async_menu_and_optional_confirmation(self) -> None:
        node_path = shutil.which("node")
        self.assertIsNotNone(node_path, "Node is required for the production-JS regression")
        replacements = {"%INVITATION_ID%": json.dumps("old"), "%EXPECTED_MODIFIED%": json.dumps("2025/11/27"),
                        "%EXPECTED_HREF%": json.dumps(TARGET_HREF)}
        templates = [invitations.OPEN_CANCELLATION_MENU_JS_TMPL, invitations.INSPECT_CANCELLATION_MENU_JS_TMPL,
                     invitations.CLICK_CANCELLATION_MENU_JS_TMPL, invitations.INSPECT_CANCELLATION_CONFIRMATION_JS_TMPL]
        for render_mode in ("microtask", "timer"):
            for confirmation_present, confirm_text, keep_text in (
                (False, "Cancel", "Keep"), (True, "Cancel", "Keep"), (True, "Withdraw", "Cancel"),
            ):
                with self.subTest(render_mode=render_mode, confirmation_present=confirmation_present,
                                  confirm_text=confirm_text):
                    selected_templates = [*templates]
                    if confirmation_present:
                        selected_templates.append(invitations.CLICK_CANCELLATION_CONFIRMATION_JS_TMPL)
                    selected_templates.append(invitations.CLEAR_CANCELLATION_ATTEMPT_JS)
                    scripts = []
                    for template in selected_templates:
                        for placeholder, value in replacements.items():
                            template = template.replace(placeholder, value)
                        scripts.append(template)
                    harness = CANCELLATION_DOM_HARNESS_JS.replace("%TARGET_HREF%", json.dumps(TARGET_HREF))
                    completed = subprocess.run(
                        [node_path, "-e", harness, json.dumps(scripts), render_mode,
                         json.dumps({"confirmation": confirmation_present,
                                     "confirmText": confirm_text, "keepText": keep_text})],
                        shell=False, capture_output=True, text=True, encoding="utf-8", timeout=10, check=True,
                    )
                    result = json.loads(completed.stdout)
                    self.assertTrue(all(response["ok"] for response in result["responses"]), result)
                    self.assertEqual(result["responses"][3]["ready"], confirmation_present)
                    self.assertEqual(result["events"], {"arrow": 1, "cancel": 1,
                        "confirm": int(confirmation_present), "keep": 0, "unrelated": 0})
                    self.assertTrue(result["stateCleared"])

    def test_async_page_size_option_allows_render_turns(self) -> None:
        self.execute.side_effect = [
            {"ok": True, "opened": True, "current": "50/页", "href": TARGET_HREF},
            {"ok": True, "ready": True}, {"ok": True, "set": True},
            {"ok": True, "ready": True, "current": "100/页"},
        ]
        invitations.ensure_page_size("store-two")
        scripts = [arguments.args[1] for arguments in self.execute.call_args_list]
        for render_mode in ("microtask", "timer"):
            with self.subTest(render_mode=render_mode):
                result = run_async_dom_scripts(scripts, render_mode)
                self.assertTrue(result["responses"][-1]["ready"], result)
                self.assertTrue(all(response["ok"] for response in result["responses"]), result)
                self.assertEqual(result["clickCounts"], [
                    {"trigger": 1, "option": 0}, {"trigger": 1, "option": 0},
                    {"trigger": 1, "option": 1}, {"trigger": 1, "option": 1},
                ])
                self.assertEqual(result["events"]["trigger"], 1)
                self.assertEqual(result["events"]["option"], 1)
                self.assertEqual(result["pageSize"], "100/页")

    def test_production_option_probes_reject_ambiguity_and_context_change_without_clicks(self) -> None:
        scripts = [invitations.SET_PAGE_SIZE_JS, *[
            template.replace("%EXPECTED_HREF%", json.dumps(TARGET_HREF))
            for template in (invitations.INSPECT_PAGE_SIZE_OPTION_JS_TMPL,
                             invitations.CLICK_PAGE_SIZE_OPTION_JS_TMPL)
        ]]
        for settings, reason in (
            ({"optionCount": 2}, "ambiguous-pagesize-option"),
            ({"changedHref": TARGET_HREF.replace("shop-two", "other-shop")}, "pagesize-context-changed"),
        ):
            with self.subTest(settings=settings):
                result = run_async_dom_scripts(scripts, "timer", **settings)
                self.assertEqual(result["events"], {"trigger": 1, "option": 0})
                self.assertEqual([response["reason"] for response in result["responses"][1:]], [reason, reason])

    def test_production_already_100_never_opens_dropdown(self) -> None:
        result = run_async_dom_scripts([invitations.SET_PAGE_SIZE_JS], "microtask", initialPageSize="100/页")
        self.assertTrue(result["responses"][0]["already"])
        self.assertEqual(result["events"], {"trigger": 0, "option": 0})


class PageSizePollingTests(OfflineTestCase):
    def test_only_read_probes_repeat_and_all_transport_calls_have_deadlines_without_retries(self) -> None:
        self.execute.side_effect = [
            {"ok": True, "opened": True, "current": "50/页", "href": TARGET_HREF},
            {"ok": True, "ready": False}, {"ok": True, "ready": True},
            {"ok": True, "set": True},
            {"ok": True, "ready": False}, {"ok": True, "ready": True, "current": "100/页"},
        ]
        result = invitations.ensure_page_size("store-two")
        self.assertEqual(result, {"ok": True, "set": True, "previous": "50/页", "current": "100/页"})
        scripts = [arguments.args[1] for arguments in self.execute.call_args_list]
        self.assertEqual(scripts.count(invitations.SET_PAGE_SIZE_JS), 1)
        self.assertEqual(scripts[1], scripts[2])
        self.assertEqual(scripts[4], scripts[5])
        self.assertNotIn(".click()", scripts[1])
        self.assertNotIn(".click()", scripts[4])
        self.assertEqual(self.sleep.call_count, 2)
        for arguments in self.execute.call_args_list:
            self.assertEqual(arguments.kwargs["retries"], 0)
            self.assertIs(arguments.kwargs["retry_timeout_expired"], False)
            self.assertGreater(arguments.kwargs["deadline"], 0)
        self.assertEqual(len({arguments.kwargs["deadline"] for arguments in self.execute.call_args_list[:4]}), 1)

    def test_lost_trigger_or_option_response_is_never_replayed(self) -> None:
        for failure_stage in ("trigger", "option"):
            with self.subTest(failure_stage=failure_stage):
                self.execute.reset_mock()
                responses = [] if failure_stage == "trigger" else [
                    {"ok": True, "opened": True, "href": TARGET_HREF}, {"ok": True, "ready": True},
                ]
                self.execute.side_effect = [*responses, TimeoutError("response lost")]
                with self.assertRaisesRegex(TimeoutError, "response lost"):
                    invitations.ensure_page_size("store-two")
                self.assertEqual(self.execute.call_count, len(responses) + 1)
                self.assertEqual(sum(arguments.args[1] == invitations.SET_PAGE_SIZE_JS
                                     for arguments in self.execute.call_args_list), 1)

    def test_blocking_probe_cannot_extend_deadline_or_click_option(self) -> None:
        current_time = [100.0]
        self.context.enter_context(patch.object(invitations.time, "monotonic", side_effect=lambda: current_time[0]))

        def execute_probe(store_id, script, **arguments):
            if script == invitations.SET_PAGE_SIZE_JS:
                return {"ok": True, "opened": True, "href": TARGET_HREF}
            current_time[0] = arguments["deadline"] + 0.1
            return {"ok": True, "ready": True}

        self.execute.side_effect = execute_probe
        with self.assertRaisesRegex(RuntimeError, "deadline expired"):
            invitations.ensure_page_size("store-two")
        self.assertEqual(self.execute.call_count, 2)
        self.sleep.assert_not_called()

    def test_option_timeout_has_bounded_read_polls_and_no_second_trigger(self) -> None:
        current_time = [100.0]
        self.context.enter_context(patch.object(invitations.time, "monotonic", side_effect=lambda: current_time[0]))
        self.sleep.side_effect = lambda duration: current_time.__setitem__(0, current_time[0] + duration)
        self.execute.side_effect = lambda store_id, script, **arguments: (
            {"ok": True, "opened": True, "href": TARGET_HREF} if script == invitations.SET_PAGE_SIZE_JS
            else {"ok": True, "ready": False}
        )
        with self.assertRaisesRegex(RuntimeError, "deadline expired"):
            invitations.ensure_page_size("store-two")
        self.assertLessEqual(self.execute.call_count, 12)
        self.assertLessEqual(current_time[0], 100.0 + invitations.PAGE_SIZE_OPTION_WAIT_SECONDS)
        self.assertEqual(sum(".click()" in arguments.args[1] for arguments in self.execute.call_args_list), 1)

    def test_unverified_label_after_option_click_never_reports_success(self) -> None:
        current_time = [100.0]
        self.context.enter_context(patch.object(invitations.time, "monotonic", side_effect=lambda: current_time[0]))
        self.sleep.side_effect = lambda duration: current_time.__setitem__(0, current_time[0] + duration)
        responses = iter([
            {"ok": True, "opened": True, "href": TARGET_HREF},
            {"ok": True, "ready": True}, {"ok": True, "set": True},
        ])
        self.execute.side_effect = lambda *arguments, **keywords: next(
            responses, {"ok": True, "ready": False, "current": "50/页"},
        )
        with self.assertRaisesRegex(RuntimeError, "deadline expired"):
            invitations.ensure_page_size("store-two")
        self.assertEqual(sum(".click()" in arguments.args[1] for arguments in self.execute.call_args_list), 2)


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
    def setUp(self):
        super().setUp()
        from lib import target_invitation_navigation as navigation
        self.context.enter_context(patch.object(navigation, "wait_for_target_shell", return_value={"href": TARGET_HREF}))
        self.context.enter_context(patch.object(navigation, "wait_for_target_list_ready", return_value={"ready": True, "href": TARGET_HREF}))
        self.raw_execute = self.execute

        def transport(store_id, script, **options):
            normalized = script.replace(
                "const expectedContext = " + json.dumps(invitations._shop_context(TARGET_HREF)) + ";",
                "const expectedContext = null;",
            )
            response = self.raw_execute(store_id, normalized, **options)
            if "error" in response:
                response = {"can_retry": False, **response}
            if normalized == invitations.CLICK_LIST_RETRY_JS and response.get("ok"):
                response = {"clicked": True, **response}
            return {"href": TARGET_HREF, **response}

        self.context.enter_context(patch.object(invitations, "zclaw_exec", transport))

    def test_recovery_order_is_ongoing_retry_100_then_last_page(self) -> None:
        self.execute.side_effect = [
            {"ok": True, "clicked": True}, {"ok": True, "already": True},
            {"ok": True, "error": True, "can_retry": True}, {"ok": True}, {"ok": True, "error": False},
            {"ok": True, "opened": True, "href": TARGET_HREF}, {"ok": True, "ready": True},
            {"ok": True, "set": True}, {"ok": True, "ready": True, "current": "100/页"},
            {"ok": True, "via": "last-btn"}, make_page(),
        ]
        result = invitations.restore_ongoing_list("store-two")
        self.assertTrue(result["nav"]["ok"])
        self.assertEqual([arguments.args[1] for arguments in self.execute.call_args_list], [
            invitations.ENSURE_ONGOING_TAB_JS, invitations.INSPECT_ONGOING_TAB_JS,
            invitations.INSPECT_LIST_ERROR_JS, invitations.CLICK_LIST_RETRY_JS, invitations.INSPECT_LIST_ERROR_JS,
            invitations.SET_PAGE_SIZE_JS,
            invitations.INSPECT_PAGE_SIZE_OPTION_JS_TMPL.replace("%EXPECTED_HREF%", json.dumps(TARGET_HREF)),
            invitations.CLICK_PAGE_SIZE_OPTION_JS_TMPL.replace("%EXPECTED_HREF%", json.dumps(TARGET_HREF)),
            invitations.INSPECT_PAGE_SIZE_JS_TMPL.replace("%EXPECTED_HREF%", json.dumps(TARGET_HREF)),
            invitations.GOTO_LAST_PAGE_JS,
            invitations._guard_recovery_script(invitations.EXTRACT_JS),
        ])
        self.assertIn("const want = 100;", invitations.SET_PAGE_SIZE_JS)

    def test_ongoing_tab_must_be_verified_after_click(self) -> None:
        self.execute.side_effect = [{"ok": True, "clicked": True}, {"ok": True, "clicked": True}]
        with self.assertRaisesRegex(RuntimeError, "not verified"):
            invitations.ensure_ongoing_tab("store-two")

    def test_page_size_failure_is_not_ignored(self) -> None:
        self.execute.side_effect = [{"ok": True, "already": True}, {"ok": True, "error": False},
                                    {"ok": False}, make_page()]
        with self.assertRaisesRegex(RuntimeError, "100 rows"):
            invitations.restore_ongoing_list("store-two")
        self.assertNotIn(invitations.GOTO_LAST_PAGE_JS, [arguments.args[1] for arguments in self.execute.call_args_list])
        self.assertEqual(sum(arguments.args[1] == invitations.SET_PAGE_SIZE_JS
                             for arguments in self.execute.call_args_list), 1)

    def test_last_page_navigation_rejects_receipt_without_replaying_action(self) -> None:
        self.execute.side_effect = [{"ok": True, "already": True}, {"ok": True, "error": False},
                                    {"ok": True, "already": True}, {"ok": False}]
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            invitations.restore_ongoing_list("store-two")
        self.assertEqual(sum(arguments.args[1] == invitations.GOTO_LAST_PAGE_JS
                             for arguments in self.execute.call_args_list), 1)

    def test_verified_empty_state_can_recover_without_a_page_size_widget(self) -> None:
        self.execute.side_effect = [{"ok": True, "already": True}, {"ok": True, "error": False},
                                    {"ok": False, "reason": "no-pagesize-control"},
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


RECOVERY_IDENTITY_HARNESS_JS = r"""
const scripts = JSON.parse(process.argv[1]);
const settings = JSON.parse(process.argv[2]);
globalThis.location = {href:settings.href};
let clicks = 0, queries = 0;
const element = text => ({innerText:text, click:() => clicks++,
  classList:{contains:() => Boolean(settings.ongoing)}, getAttribute:() => null,
  getBoundingClientRect:() => ({width:100, height:20})});
globalThis.document = {title:settings.title || '', readyState:'complete',
  body:{innerText:settings.error ? 'Something went wrong' : 'Ongoing'},
  querySelector:selector => {
    queries++;
    if (selector.includes('select-view-value')) return element('50/页');
    return settings.error && selector.includes('pagination-item-next') ? null : element('1');
  },
  querySelectorAll:selector => {
    queries++;
    if (selector === 'table tbody tr') return [];
    if (selector.includes('role=tab')) return settings.noTab ? [] : [element('Ongoing')];
    if (selector.includes('role=option')) return [element('100/页')];
    if (selector.includes('role=button')) return [element('Retry')];
    return [];
  }
};
const responses = scripts.map(script => JSON.parse(eval(script)));
process.stdout.write(JSON.stringify({responses, clicks, queries}));
"""


class RecoveryJavascriptTests(unittest.TestCase):
    def execute_scripts(self, scripts, **settings):
        node_path = shutil.which("node")
        self.assertIsNotNone(node_path, "Node is required for production-JS identity tests")
        completed = subprocess.run(
            [node_path, "-e", RECOVERY_IDENTITY_HARNESS_JS, json.dumps(scripts),
             json.dumps({"href": TARGET_HREF, **settings})],
            shell=False, capture_output=True, text=True, encoding="utf-8", timeout=10, check=True,
        )
        return json.loads(completed.stdout)

    def test_every_recovery_side_effect_checks_identity_inside_its_script(self):
        context = invitations._shop_context(TARGET_HREF)
        scripts = [invitations._bind_recovery_context(source, context) for source in (
            invitations.ENSURE_ONGOING_TAB_JS, invitations.CLICK_LIST_RETRY_JS,
            invitations.SET_PAGE_SIZE_JS, invitations.GOTO_LAST_PAGE_JS,
            invitations.CLICK_PAGE_SIZE_OPTION_JS_TMPL.replace("%EXPECTED_HREF%", json.dumps(TARGET_HREF)),
        )]
        valid = self.execute_scripts(scripts)
        self.assertTrue(all(response["ok"] for response in valid["responses"]))
        self.assertEqual(valid["clicks"], 5)
        bad_hrefs = (
            TARGET_HREF.replace("shop-two", "changed"), TARGET_HREF.replace("shop_region=US", "shop_region=GB"),
            TARGET_HREF + "&shop_id=other", TARGET_HREF + "&shop_id=", TARGET_HREF + "&shop_region=",
            TARGET_HREF.replace("shop_id=shop-two", "shop_id="), TARGET_HREF.replace("shop_region=US", "shop_region="),
            TARGET_HREF.replace("https:", "http:"), TARGET_HREF.replace("affiliate.tiktokshopglobalselling.com", "example.invalid"),
            TARGET_HREF.replace("/affiliate/collaboration/target-invitation", "/unknown/target-invitation"),
            "https://affiliate.tiktokshopglobalselling.com/login", "about:blank",
        )
        for href in bad_hrefs:
            with self.subTest(href=href):
                result = self.execute_scripts(scripts, href=href)
                self.assertTrue(all(response.get("reason") == "list-context-changed" for response in result["responses"]))
                self.assertEqual(result["clicks"], 0)
                self.assertEqual(result["queries"], 0)
        login_overlay = self.execute_scripts(scripts, title="Log in to TikTok Shop")
        self.assertEqual(login_overlay["clicks"], 0)
        self.assertTrue(all(not response["ok"] for response in login_overlay["responses"]))

    def test_recoverable_shell_never_claims_error_list_is_ready(self):
        from lib import target_invitation_navigation as navigation
        scripts = [navigation.INSPECT_TARGET_SHELL_JS, navigation.INSPECT_TARGET_LIST_READINESS_JS]
        result = self.execute_scripts(scripts, error=True)
        self.assertTrue(result["responses"][0]["shell_ready"])
        self.assertFalse(result["responses"][1]["ready"])
        self.assertEqual(result["clicks"], 0)
        missing_tab = self.execute_scripts(scripts, noTab=True)
        self.assertFalse(missing_tab["responses"][0]["shell_ready"])
        self.assertFalse(missing_tab["responses"][1]["ready"])


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
        self.extract = self.context.enter_context(patch.object(invitations, "extract_page", return_value=make_page()))
        self.restore = self.context.enter_context(patch.object(invitations, "restore_ongoing_list", return_value=RESTORED))
        self.wait = self.context.enter_context(patch.object(invitations, "_wait_for_ui_ready"))
        self.wait.side_effect = [{"ok": True, "ready": True}, {"ok": True, "ready": False}]
        self.execute.side_effect = [
            {"ok": True, "opened": True}, {"ok": True, "invitation_id": "old", "clickedCancel": True},
            {"ok": True},
        ]

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
        self.wait.assert_not_called()

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
        self.wait.assert_not_called()

    def test_no_confirmation_is_a_valid_submission_and_list_is_restored(self) -> None:
        result = self.cancel(execute=True)
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["write_attempted"])
        self.assertEqual(result["reason"], "operation-submitted")
        self.assertFalse(result["confirmed"])
        self.assertIsNone(result["gone"])
        self.extract.assert_called_once_with("store-two")
        self.restore.assert_called_once_with("store-two", expected_context=invitations._shop_context(TARGET_HREF))
        self.assertFalse(self.wait.call_args.kwargs["required"])
        self.assertEqual(self.execute.call_count, 3)
        for arguments in self.execute.call_args_list:
            self.assertEqual(arguments.kwargs, {"retries": 0, "retry_timeout_expired": False})

    def test_optional_confirmation_is_clicked_once_when_present(self) -> None:
        self.wait.side_effect = [{"ok": True, "ready": True}, {"ok": True, "ready": True}]
        self.execute.side_effect = [
            {"ok": True, "opened": True}, {"ok": True, "invitation_id": "old", "clickedCancel": True},
            {"ok": True, "confirmed": True, "confirmText": "Cancel"}, {"ok": True},
        ]
        result = self.cancel(execute=True)
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["confirmed"])
        self.assertEqual(self.execute.call_count, 4)
        self.restore.assert_called_once()

    def test_prewrite_read_failure_never_reaches_a_click_or_unknown_write(self) -> None:
        self.extract.side_effect = RuntimeError("Bridge read failed")
        result = self.cancel(execute=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "prewrite-read-failed")
        self.assertFalse(result["write_attempted"])
        self.execute.assert_not_called()
        self.restore.assert_not_called()
        self.wait.assert_not_called()

    def test_lost_cancellation_or_confirmation_response_is_never_replayed(self) -> None:
        for stage in ("cancel", "confirm"):
            with self.subTest(stage=stage):
                self.execute.reset_mock()
                self.restore.reset_mock()
                self.wait.side_effect = [{"ok": True, "ready": True}, {"ok": True, "ready": True}]
                responses = [{"ok": True, "opened": True}]
                if stage == "confirm":
                    responses.append({"ok": True, "invitation_id": "old", "clickedCancel": True})
                self.execute.side_effect = [*responses, TimeoutError("lost click receipt"), {"ok": True}]
                result = self.cancel(execute=True)
                self.assertEqual(result["status"], "uncertain")
                self.assertTrue(result["write_attempted"])
                self.assertEqual(self.execute.call_count, len(responses) + 2)
                self.restore.assert_called_once()

    def test_menu_timeout_never_clicks_cancel(self) -> None:
        self.wait.side_effect = RuntimeError("UI readiness deadline expired")
        self.execute.side_effect = [{"ok": True, "opened": True}, {"ok": True}]
        result = self.cancel(execute=True)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["write_attempted"])
        self.assertEqual(self.execute.call_count, 2)

    def test_recovery_failure_preserves_unknown_write(self) -> None:
        self.restore.side_effect = RuntimeError("list recovery failed")
        result = self.cancel(execute=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["reason"], "postwrite-recovery-failed")
        self.assertTrue(result["write_attempted"])

    def test_hostile_id_is_substituted_once_as_data(self) -> None:
        invitation_id = 'fixed-%EXPECTED_HREF%-%DO_CANCEL%-"'
        self.extract.return_value = make_page(rows=[make_row(invitation_id)])
        self.execute.side_effect = [{"ok": False, "reason": "no-arrow"}, {"ok": True}]
        result = invitations.cancel_invitation_by_id(
            "store-two", invitation_id=invitation_id, expected_last_modified="2025/11/27", cutoff=CUTOFF, execute=True,
        )
        self.assertFalse(result["write_attempted"])
        self.assertIn("const targetId = " + json.dumps(invitation_id) + ";", self.execute.call_args_list[0].args[1])


if __name__ == "__main__":
    unittest.main()
