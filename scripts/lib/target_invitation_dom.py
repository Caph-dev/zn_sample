"""Bounded target-invitation DOM operations using the existing ZClaw transport.

Origin: zn_daren/scripts/lib/zclaw_dom.py at 2d8e21c plus its 2026-10-05
worktree date-regex change accepting optional English "on". Selectors, date
extraction, reverse pagination and recovery order come from that source. Unlike
its broad cancel success/retry behavior, this migration has no write retry block.
Missing pagination evidence fails closed instead of inventing a selector.

The caller owns store/shop binding, immutable candidate validation and the
write-ahead ``attempting`` checkpoint. Locate first, persist that checkpoint,
then cancel the same fixed ID. No candidate discovery occurs during execution.
"""
from __future__ import annotations

import calendar
import json
import logging
import re
import time
import urllib.parse
from datetime import date
from typing import Any, Mapping, TypedDict

from .zclaw import zclaw_exec

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_PAGES = 50
CANCEL_DELAY_SECONDS = 1.2
PAGE_WAIT_SECONDS = 1.2


class InvitationScanResult(TypedDict):
    rows: list[dict[str, Any]]
    scan_complete: bool
    stop_reason: str
    pages_scanned: int


class InvitationLocationResult(TypedDict):
    found: bool
    revalidated: bool
    reason: str
    row: dict[str, Any] | None
    pages_scanned: int


class InvitationCancellationResult(TypedDict):
    status: str
    action: str
    confirmed: bool | None
    gone: bool | None
    write_attempted: bool
    reason: str
    raw: Any


# Shared extraction ensures the final write guard uses the same displayed date
# and silent React record.id as the preview, never names or row_key fallback.
INVITATION_DOM_HELPERS_JS = r"""
function getRecord(tableRow) {
  const fiberKey = Object.keys(tableRow || {}).find(propertyName =>
    propertyName.startsWith('__reactFiber') || propertyName.startsWith('__reactInternalInstance')
  );
  if (!fiberKey) return null;
  let fiber = tableRow[fiberKey];
  for (let depth = 0; depth < 40 && fiber; depth++) {
    const properties = fiber.memoizedProps;
    if (properties && properties.record && properties.record.id != null) return properties.record;
    fiber = fiber.return;
  }
  return null;
}
function extractRow(tableRow, rowIndex) {
  const cells = [...tableRow.querySelectorAll('td')];
  if (cells.length < 3) return null;
  const firstCellText = (cells[0].innerText || '').trim();
  const record = getRecord(tableRow);
  const name = record && record.name ? String(record.name) :
    (firstCellText.split('\n')[0] || '').trim().replace(/\s*\.\s*$/, '').replace(/\s+ID\s*$/i, '').trim();
  if (!name && !(record && record.id)) return null;
  const modifiedLine = firstCellText.match(/(?:上次修改时间|last\s+(?:modified|updated)(?:\s+(?:time|date))?)\s*[：:]?\s*(?:on\s+)?([^\n\r]+)/i);
  const modifiedText = modifiedLine ? modifiedLine[1].trim() : '';
  const numericDate = modifiedText.match(/^(\d{4}[/\.\-]\d{1,2}[/\.\-]\d{1,2})(?!\d)/);
  const lastModified = numericDate ? numericDate[1] : modifiedText;
  const invitedCount = (firstCellText.match(/已邀请\s*(\d+)\s*位达人/) || [])[1]
    || (record && record.creator_cnt != null ? String(record.creator_cnt) : '');
  const acceptedCount = record && record.creator_added_cnt != null ? String(record.creator_added_cnt)
    : ((cells[1].innerText || '').match(/(\d+)/) || [])[1] || '';
  const promotedCount = record && record.creator_posted_cnt != null ? String(record.creator_posted_cnt)
    : ((cells[2].innerText || '').match(/(\d+)/) || [])[1] || '';
  const invitationId = record && record.id != null ? String(record.id) : '';
  return {
    row_index: rowIndex, invitation_id: invitationId, name, last_modified: lastModified,
    product_count: (firstCellText.match(/(\d+)\s*件商品/) || [])[1]
      || (record && record.product_cnt != null ? String(record.product_cnt) : ''),
    invited_count: invitedCount, accepted_count: acceptedCount, promoted_count: promotedCount,
    row_key: invitationId || [name, lastModified, invitedCount].join('|')
  };
}
function isDisabled(control) {
  return !!control && (
    (control.classList && (control.classList.contains('core-pagination-item-disabled')
      || control.classList.contains('arco-pagination-item-disabled')))
    || control.getAttribute('aria-disabled') === 'true' || control.hasAttribute('disabled')
  );
}
function inspectListContext() {
  const bodyText = document.body && document.body.innerText || '';
  const active = document.querySelector('.core-pagination-item-active, .arco-pagination-item-active');
  const nextControl = document.querySelector(
    '.core-pagination-item-next, .arco-pagination-item-next, li[aria-label="下一页"], button[aria-label="下一页"]'
  );
  const previousControl = document.querySelector(
    '.core-pagination-item-prev, .arco-pagination-item-prev, li[aria-label="上一页"], button[aria-label="上一页"]'
  );
  const ongoingTab = [...document.querySelectorAll('.core-tabs-header-title, [role=tab]')].find(element => {
    const text = (element.innerText || '').trim().replace(/\s+/g, ' ');
    return (/^(进行中|Ongoing)\b/i.test(text) || text === '进行中' || /^进行中\s*\d+/.test(text))
      && element.getBoundingClientRect().width > 0;
  });
  const ongoing = !!ongoingTab && (ongoingTab.classList.contains('core-tabs-header-title-active')
    || ongoingTab.getAttribute('aria-selected') === 'true');
  const pageSizeControl = document.querySelector('.core-pagination-option .core-select-view-value');
  return {
    href: location.href || '', title: document.title || '', ready_state: document.readyState,
    page: active ? (active.innerText || '').trim() : '',
    total: (bodyText.match(/总行数[：:]\s*(\d+)/) || [])[1] || '',
    ongoing, has_empty_state: /暂无数据|No data|没有数据/i.test(bodyText),
    list_error: /哎呀[！!]\s*出错了|网络错误|请重试|Network error|Something went wrong/i.test(bodyText),
    page_size: (pageSizeControl && pageSizeControl.innerText || '').trim(),
    next_control_present: !!nextControl, next_disabled: isDisabled(nextControl),
    previous_control_present: !!previousControl, previous_disabled: isDisabled(previousControl)
  };
}
"""

EXTRACT_JS = "(() => {\n" + INVITATION_DOM_HELPERS_JS + r"""
  if (!document.body) return JSON.stringify({ok:false, reason:'no-document-body', href:location.href || '', rows:[]});
  const rows = [...document.querySelectorAll('table tbody tr')].map(extractRow).filter(Boolean);
  const context = inspectListContext();
  return JSON.stringify({...context, ok:true, count:rows.length, rows,
    canNext:context.next_control_present && !context.next_disabled});
})()
"""

CLICK_PREV_JS = r"""
(() => {
  const previous = document.querySelector(
    '.core-pagination-item-prev:not(.core-pagination-item-disabled), ' +
    '.arco-pagination-item-prev:not(.arco-pagination-item-disabled), ' +
    'li[aria-label="上一页"]:not([aria-disabled="true"]), ' +
    'button[aria-label="上一页"]:not([disabled])'
  );
  if (!previous) return JSON.stringify({ok:false, reason:'no-prev'});
  if ((previous.classList && (previous.classList.contains('core-pagination-item-disabled')
    || previous.classList.contains('arco-pagination-item-disabled')))
    || previous.getAttribute('aria-disabled') === 'true' || previous.hasAttribute('disabled')) {
    return JSON.stringify({ok:false, reason:'prev-disabled'});
  }
  previous.click();
  return JSON.stringify({ok:true});
})()
"""

GOTO_LAST_PAGE_JS = r"""
(() => {
  const active = document.querySelector('.core-pagination-item-active, .arco-pagination-item-active');
  const current = active ? (active.innerText || '').trim() : '';
  const lastButton = document.querySelector(
    '.core-pagination-item-last:not(.core-pagination-item-disabled), ' +
    '.arco-pagination-item-last:not(.arco-pagination-item-disabled), ' +
    'li[aria-label="末页"]:not([aria-disabled="true"]), button[aria-label="末页"]:not([disabled])'
  );
  if (lastButton) {
    lastButton.click();
    return JSON.stringify({ok:true, via:'last-btn', from:current});
  }
  const items = [...document.querySelectorAll('.core-pagination-item, .arco-pagination-item, li[class*="pagination"]')];
  let lastItem = null, lastNumber = -1;
  for (const element of items) {
    const text = (element.innerText || '').trim();
    if (!/^\d+$/.test(text) || /next|prev|disabled|jump/i.test(String(element.className || ''))) continue;
    const pageNumber = parseInt(text, 10);
    if (pageNumber > lastNumber) { lastNumber = pageNumber; lastItem = element; }
  }
  if (!lastItem) return JSON.stringify({ok:false, reason:'last-page-control-not-found'});
  if (String(lastNumber) === current) return JSON.stringify({ok:true, already:true, page:lastNumber});
  lastItem.click();
  return JSON.stringify({ok:true, via:'max-page-item', page:lastNumber, from:current});
})()
"""

SET_PAGE_SIZE_JS = r"""
(() => {
  const want = 100;
  const label = want + '/页';
  const valueElement = document.querySelector('.core-pagination-option .core-select-view-value');
  const current = (valueElement && valueElement.innerText || '').trim();
  if (current === label || current === String(want) || current === (want + ' / 页')) {
    return JSON.stringify({ok:true, already:true, current});
  }
  const trigger = document.querySelector('.core-pagination-option .core-select-view')
    || document.querySelector('.core-pagination-option .core-select')
    || document.querySelector('.core-pagination-option');
  if (!trigger) return JSON.stringify({ok:false, reason:'no-pagesize-control', current});
  trigger.click();
  const deadline = Date.now() + 2500;
  let option = null;
  while (Date.now() < deadline) {
    option = [...document.querySelectorAll('li[role=option], .core-select-option, div[role=option], .arco-select-option, li')]
      .find(element => {
        const text = (element.innerText || '').trim();
        return text === label || text === String(want) || text === (want + ' / 页');
      });
    if (option) break;
  }
  if (!option) {
    document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
    return JSON.stringify({ok:false, reason:'option-not-found', current});
  }
  option.click();
  return JSON.stringify({ok:true, set:true, previous:current});
})()
"""

INSPECT_LIST_ERROR_JS = r"""
(() => {
  const bodyText = document.body && document.body.innerText || '';
  const rowCount = document.querySelectorAll('table tbody tr').length;
  const retry = [...document.querySelectorAll('button, a, [role=button]')].find(element => {
    const text = (element.innerText || '').trim().replace(/\s+/g, ' ');
    const rectangle = element.getBoundingClientRect();
    return rectangle.width > 0 && rectangle.height > 0 && /^(重试|Retry|Try again)$/i.test(text);
  });
  return JSON.stringify({ok:true,
    error:/哎呀[！!]\s*出错了|网络错误|请重试|Network error|Something went wrong/i.test(bodyText) && rowCount === 0,
    can_retry:!!retry, href:location.href || '', row_count:rowCount});
})()
"""

CLICK_LIST_RETRY_JS = r"""
(() => {
  const retry = [...document.querySelectorAll('button, a, [role=button]')].find(element => {
    const text = (element.innerText || '').trim().replace(/\s+/g, ' ');
    const rectangle = element.getBoundingClientRect();
    return rectangle.width > 0 && rectangle.height > 0 && /^(重试|Retry|Try again)$/i.test(text);
  });
  if (!retry) return JSON.stringify({ok:false, reason:'retry-not-found'});
  retry.click();
  return JSON.stringify({ok:true, clicked:true});
})()
"""

ENSURE_ONGOING_TAB_JS = r"""
(() => {
  const ongoingTab = [...document.querySelectorAll('.core-tabs-header-title, [role=tab]')].find(element => {
    const text = (element.innerText || '').trim().replace(/\s+/g, ' ');
    return (/^(进行中|Ongoing)\b/i.test(text) || text === '进行中' || /^进行中\s*\d+/.test(text))
      && element.getBoundingClientRect().width > 0;
  });
  if (!ongoingTab) return JSON.stringify({ok:false, reason:'ongoing-tab-not-found', href:location.href});
  const already = ongoingTab.classList.contains('core-tabs-header-title-active')
    || ongoingTab.getAttribute('aria-selected') === 'true';
  if (already) return JSON.stringify({ok:true, already:true, href:location.href});
  ongoingTab.click();
  return JSON.stringify({ok:true, clicked:true, href:location.href});
})()
"""

PREPARE_LIST_JS = r"""
(() => {
  const containers = [document.querySelector('.core-table-body'), document.querySelector('.arco-table-body'),
    document.querySelector('[class*="table-body"]'), document.querySelector('.core-spin-children'), document.querySelector('main')].filter(Boolean);
  for (const container of containers) {
    try { container.scrollTop = 0; container.scrollTop = container.scrollHeight; container.scrollTop = 0; } catch (error) {}
  }
  window.scrollTo(0, 0);
  return JSON.stringify({ok:true, rowCount:document.querySelectorAll('table tbody tr').length});
})()
"""

CANCEL_INVITE_JS_TMPL = "(() => {\n" + INVITATION_DOM_HELPERS_JS + r"""
  const targetId = %INVITATION_ID%;
  const expectedModified = %EXPECTED_MODIFIED%;
  const expectedHref = %EXPECTED_HREF%;
  const expectedPage = %EXPECTED_PAGE%;
  const doCancel = %DO_CANCEL%;
  const reject = reason => JSON.stringify({ok:false, reason, invitation_id:targetId, write_attempted:false});
  if (!document.body || !targetId) return reject('missing-document-or-id');
  const context = inspectListContext();
  if (!context.ongoing || context.list_error || context.ready_state !== 'complete'
    || context.href !== expectedHref || context.page !== expectedPage) return reject('list-context-changed');
  const matches = [...document.querySelectorAll('table tbody tr')].filter(tableRow => {
    const record = getRecord(tableRow);
    return record && String(record.id) === targetId;
  });
  if (matches.length !== 1) return reject(matches.length ? 'duplicate-invitation-id' : 'row-not-found');
  const tableRow = matches[0];
  const row = extractRow(tableRow, 0);
  if (!row || row.last_modified !== expectedModified) return reject('last-modified-changed');
  if (!doCancel) return JSON.stringify({ok:true, dry:true, invitation_id:targetId, write_attempted:false});
  try { tableRow.scrollIntoView({block:'center', inline:'nearest'}); } catch (error) {}
  const cells = [...tableRow.querySelectorAll('td')];
  const operationCell = cells[cells.length - 1];
  if (!operationCell) return reject('no-operation-cell');
  const buttons = [...operationCell.querySelectorAll('button')];
  let arrow = buttons.find(button => !(button.innerText || '').trim()
    || (button.innerText || '').includes('∨') || (button.innerText || '').includes('▼'));
  if (!arrow && buttons.length >= 2) arrow = buttons[1];
  if (!arrow && buttons.length === 1) arrow = buttons[0];
  if (!arrow) return reject('no-arrow');
  arrow.click();
  const menuDeadline = Date.now() + 3000;
  let menuItem = null;
  while (Date.now() < menuDeadline) {
    menuItem = [...document.querySelectorAll('li,div,span,button,a')].find(element => {
      const text = (element.innerText || '').trim();
      const rectangle = element.getBoundingClientRect();
      return (text === '取消邀请' || text === 'Cancel invitation' || text === 'Cancel Invitation')
        && rectangle.width > 0 && rectangle.height > 0 && rectangle.width < 400;
    });
    if (menuItem) break;
  }
  if (!menuItem) return reject('menu-item-not-found');
  menuItem.click();
  const confirmationDeadline = Date.now() + 4000;
  let confirmed = false, confirmText = '';
  while (Date.now() < confirmationDeadline) {
    const confirmationButtons = [...document.querySelectorAll('button')].filter(button => {
      const text = (button.innerText || '').trim();
      const rectangle = button.getBoundingClientRect();
      return rectangle.width > 0 && rectangle.height > 0
        && (text === '确定' || text === '确认' || text === 'OK' || text === 'Confirm' || text === 'Yes'
          || text === '取消邀请' || text === 'Confirm cancel' || /^确定/.test(text));
    });
    // An unchanged menu item is not independent confirmation evidence.
    const confirmation = confirmationButtons.find(button => /^(确定|确认|OK|Confirm|Yes)/i.test((button.innerText || '').trim()));
    if (confirmation) {
      confirmText = (confirmation.innerText || '').trim();
      confirmation.click();
      confirmed = true;
      break;
    }
  }
  return JSON.stringify({ok:true, dry:false, invitation_id:targetId, write_attempted:true,
    clickedCancel:true, confirmed, confirmText, matchCount:matches.length});
})()
"""


def parse_ui_date(text: str) -> date | None:
    """Source date semantics: unambiguous year-first or English month names."""
    normalized = str(text or "").strip()
    numeric = re.fullmatch(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", normalized)
    try:
        if numeric:
            return date(*(int(component) for component in numeric.groups()))
        words = normalized.replace(",", " ").split()
        if len(words) != 3 or not re.fullmatch(r"\d{4}", words[2]):
            return None
        month_word, day_word, year_word = words
        if month_word.isdigit():
            day_word, month_word = month_word, day_word
        month_names = (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        )
        for month_number, month_name in enumerate(month_names, start=1):
            if month_word.lower() in (month_name, month_name[:3]) and day_word.isdigit():
                return date(int(year_word), month_number, int(day_word))
    except ValueError:
        return None
    return None


def months_ago(run_date: date, months: int) -> date:
    """Fixed presets, preserving source natural-month/end-of-month clamping."""
    if type(months) is not int or months not in {2, 4}:
        raise ValueError("months must be the integer 2 or 4")
    month_index = run_date.year * 12 + run_date.month - 1 - months
    target_year, zero_based_month = divmod(month_index, 12)
    target_month = zero_based_month + 1
    target_day = min(run_date.day, calendar.monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def evaluate_invitation(row: Mapping[str, Any], *, cutoff: date) -> tuple[bool, str]:
    """Age-only rule, strict boundary; creator counts are intentionally ignored."""
    _validate_cutoff(cutoff)
    modified_date = parse_ui_date(str(row.get("last_modified") or ""))
    if modified_date is None:
        return False, "unparsed-last-modified"
    if not str(row.get("invitation_id") or "").strip():
        return False, "missing-invitation-id"
    if modified_date >= cutoff:
        return False, "on-or-after-cutoff"
    return True, "older-than-cutoff"


def scrape_date_decision(last_modified: str, cutoff: date | None) -> str:
    if cutoff is None:
        return "keep"
    modified_date = parse_ui_date(last_modified)
    if modified_date is None:
        return "unparsed"
    return "too_new" if modified_date >= cutoff else "keep"


def row_identity(row: Mapping[str, Any]) -> str:
    """Deduplication only. The fallback must never be used as a cancel ID."""
    invitation_id = str(row.get("invitation_id") or "").strip()
    return invitation_id or str(row.get("row_key") or "") or "|".join(
        str(row.get(field) or "") for field in ("name", "last_modified", "invited_count")
    )


def accumulate_scrape_page(
    page_rows: list[dict[str, Any]], *, page_no: Any, seen: set[str], cutoff: date | None,
) -> tuple[list[dict[str, Any]], int, bool]:
    added: list[dict[str, Any]] = []
    missing_id = 0
    page_has_too_new = False
    for row in page_rows:
        item = {**row, "scrape_page": page_no, "page": page_no}
        if not str(item.get("invitation_id") or "").strip():
            missing_id += 1
        identity = row_identity(item)
        if identity in seen or not (item.get("name") or item.get("invitation_id")):
            continue
        page_has_too_new |= scrape_date_decision(str(item.get("last_modified") or ""), cutoff) == "too_new"
        seen.add(identity)
        added.append(item)
    return added, missing_id, page_has_too_new


def _validate_cutoff(cutoff: date) -> None:
    if type(cutoff) is not date:
        raise ValueError("cutoff must be the frozen calendar date")


def ensure_ongoing_tab(store_id: str, *, page_wait: float = PAGE_WAIT_SECONDS) -> dict[str, Any]:
    result = zclaw_exec(store_id, ENSURE_ONGOING_TAB_JS)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(f"Cannot select ongoing invitations: {result!r}")
    if result.get("clicked"):
        time.sleep(page_wait)
        result = zclaw_exec(store_id, ENSURE_ONGOING_TAB_JS)
    if not isinstance(result, dict) or result.get("already") is not True:
        raise RuntimeError(f"Ongoing invitations tab not verified: {result!r}")
    return result


def ensure_page_size(store_id: str) -> dict[str, Any]:
    result = zclaw_exec(store_id, SET_PAGE_SIZE_JS)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(f"Cannot restore 100 rows per page: {result!r}")
    if result.get("set"):
        time.sleep(PAGE_WAIT_SECONDS)
    return result


def recover_list_if_error(store_id: str) -> dict[str, Any]:
    """Only the source's observed list-error Retry action; never reopen a store."""
    for attempt in range(4):
        state = zclaw_exec(store_id, INSPECT_LIST_ERROR_JS)
        if not isinstance(state, dict) or state.get("ok") is not True:
            raise RuntimeError(f"Cannot inspect list error state: {state!r}")
        if state.get("error") is False:
            return {"ok": True, "needed": attempt > 0, "state": state}
        if attempt == 3 or state.get("can_retry") is not True:
            raise RuntimeError(f"List error not recovered: {state!r}")
        logger.warning("Recovering target invitation list error (%s/3)", attempt + 1)
        clicked = zclaw_exec(store_id, CLICK_LIST_RETRY_JS)
        if not isinstance(clicked, dict) or clicked.get("ok") is not True:
            raise RuntimeError(f"List retry failed: {clicked!r}")
        time.sleep(2.5 * (attempt + 1))
    raise AssertionError("Unreachable recovery state")


def restore_ongoing_list(store_id: str) -> dict[str, Any]:
    """Required order: ongoing, list recovery, fixed 100/page, last page."""
    tab = ensure_ongoing_tab(store_id)
    recovered = recover_list_if_error(store_id)
    try:
        paging = ensure_page_size(store_id)
    except RuntimeError:
        recover_list_if_error(store_id)
        try:
            paging = ensure_page_size(store_id)
        except RuntimeError:
            empty_state = extract_page(store_id)
            if not empty_state["rows"] and not _page_problem(empty_state, None):
                return {"tab": tab, "recovered": recovered, "paging": {"ok": True, "empty": True},
                        "nav": {"ok": True, "empty": True}}
            raise
    navigation: Any = None
    for attempt in range(4):
        navigation = zclaw_exec(store_id, GOTO_LAST_PAGE_JS)
        if isinstance(navigation, dict) and navigation.get("ok") is True:
            time.sleep(PAGE_WAIT_SECONDS)
            break
        if attempt < 3:
            time.sleep(PAGE_WAIT_SECONDS)
    if not isinstance(navigation, dict) or navigation.get("ok") is not True:
        empty_state = extract_page(store_id)
        if not empty_state["rows"] and not _page_problem(empty_state, None):
            navigation = {"ok": True, "empty": True}
    return {"tab": tab, "recovered": recovered, "paging": paging, "nav": navigation}


def extract_page(store_id: str) -> dict[str, Any]:
    """Bounded same-page date rereads; preserve unknown text for scan reports."""
    for attempt in range(3):
        result = zclaw_exec(store_id, EXTRACT_JS)
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            raise RuntimeError(f"Invalid invitation extraction: {result!r}"[:300])
        if result.get("reason") == "no-document-body":
            raise RuntimeError("Invitation list document.body is missing")
        if result.get("ok") is False or any(not isinstance(row, dict) for row in result["rows"]):
            raise RuntimeError(f"Invitation extraction failed: {result!r}"[:300])
        if all(parse_ui_date(str(row.get("last_modified") or "")) is not None for row in result["rows"]):
            return result
        if attempt < 2:
            time.sleep(1.0)
    return result


def _shop_context(href: str) -> tuple[str, str, str] | None:
    parsed = urllib.parse.urlsplit(href)
    if parsed.scheme != "https" or parsed.hostname != "affiliate.tiktokshopglobalselling.com":
        return None
    if parsed.path.rstrip("/") not in {
        "/affiliate/collaboration/target-invitation", "/connection/target-invitation",
    }:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if any(len(query.get(field) or []) != 1 for field in ("shop_id", "shop_region")):
        return None
    shop_id = (query.get("shop_id") or [""])[0].strip()
    shop_region = (query.get("shop_region") or [""])[0].strip().upper()
    return (parsed.hostname, shop_id, shop_region) if shop_id and shop_region else None


def _page_number(data: Mapping[str, Any]) -> int | None:
    value = str(data.get("page") or "").strip()
    return int(value) if re.fullmatch(r"[1-9]\d*", value) else None


def _page_problem(data: Mapping[str, Any], context: tuple[str, str, str] | None) -> str:
    actual_context = _shop_context(str(data.get("href") or ""))
    if actual_context is None or (context is not None and actual_context != context):
        return "list-context-changed"
    if data.get("ongoing") is not True:
        return "ongoing-tab-unverified"
    if data.get("ready_state") != "complete" or data.get("list_error") is not False:
        return "list-not-ready"
    if not data["rows"]:
        total_text = str(data.get("total") or "")
        if total_text.isdigit() and int(total_text) > 0:
            return "empty-state-unverified"
        return "" if data.get("has_empty_state") is True else "empty-state-unverified"
    if _page_number(data) is None:
        return "page-number-unverified"
    if data.get("page_size") not in {"100/页", "100 / 页", "100"}:
        return "page-size-unverified"
    if len(data["rows"]) > PAGE_SIZE:
        return "page-size-exceeded"
    total_text = str(data.get("total") or "")
    if total_text.isdigit():
        page_number = _page_number(data)
        expected_count = min(PAGE_SIZE, max(0, int(total_text) - (page_number - 1) * PAGE_SIZE))
        if len(data["rows"]) != expected_count:
            return "page-rows-incomplete"
    invitation_ids = [str(row.get("invitation_id") or "") for row in data["rows"]]
    known_ids = [invitation_id for invitation_id in invitation_ids if invitation_id]
    if len(known_ids) != len(set(known_ids)):
        return "duplicate-invitation-id"
    return ""


def _at_first_page(data: Mapping[str, Any]) -> bool:
    return (_page_number(data) == 1 and data.get("previous_control_present") is True
            and data.get("previous_disabled") is True)


def _at_last_page(data: Mapping[str, Any]) -> bool:
    page_number = _page_number(data)
    total_text = str(data.get("total") or "")
    if total_text.isdigit() and int(total_text) > 0:
        last_page_number = (int(total_text) + PAGE_SIZE - 1) // PAGE_SIZE
        if page_number != last_page_number:
            return False
    return (page_number is not None and data.get("next_control_present") is True
            and data.get("next_disabled") is True)


def _read_previous_page(store_id: str, previous: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    navigation = zclaw_exec(store_id, CLICK_PREV_JS)
    if not isinstance(navigation, dict) or navigation.get("ok") is not True:
        return None, "previous-page-failed"
    time.sleep(PAGE_WAIT_SECONDS)
    previous_number = _page_number(previous)
    for poll_number in range(12):
        candidate = extract_page(store_id)
        if _page_number(candidate) != previous_number:
            if previous_number is None or _page_number(candidate) != previous_number - 1:
                return candidate, "page-sequence-changed"
            return candidate, ""
        if poll_number < 11:
            time.sleep(0.35)
    return None, "pagination-stalled"


def scan_older_invitations(store_id: str, *, cutoff: date) -> InvitationScanResult:
    """Reverse scan, fixed bounds, frozen cutoff; never performs cancellation."""
    _validate_cutoff(cutoff)
    result: InvitationScanResult = {"rows": [], "scan_complete": False, "stop_reason": "", "pages_scanned": 0}
    seen: set[str] = set()
    page_signatures: set[tuple[str, ...]] = set()
    try:
        restored = restore_ongoing_list(store_id)
        navigation = restored.get("nav")
        if not isinstance(navigation, dict) or navigation.get("ok") is not True:
            result["stop_reason"] = "last-page-navigation-failed"
            empty_state = extract_page(store_id)
            if not empty_state["rows"] and not _page_problem(empty_state, None):
                result.update(scan_complete=True, stop_reason="empty-list", pages_scanned=1)
            return result
        zclaw_exec(store_id, PREPARE_LIST_JS)
        data = extract_page(store_id)
        context = _shop_context(str(data.get("href") or ""))
        for page_index in range(MAX_PAGES):
            result["pages_scanned"] += 1
            added, missing_id_count, page_has_too_new = accumulate_scrape_page(
                data["rows"], page_no=data.get("page"), seen=seen, cutoff=cutoff,
            )
            result["rows"].extend(added)
            if missing_id_count:
                logger.warning("Invitation page %s has %s rows without record.id", data.get("page"), missing_id_count)
            problem = _page_problem(data, context)
            if problem:
                result["stop_reason"] = problem
                return result
            if not data["rows"]:
                if page_index == 0:
                    result.update(scan_complete=True, stop_reason="empty-list")
                else:
                    result["stop_reason"] = "unexpected-empty-page"
                return result
            if page_index == 0 and not _at_last_page(data):
                result["stop_reason"] = "last-page-unverified"
                return result
            signature = tuple(row_identity(row) for row in data["rows"])
            if signature in page_signatures:
                result["stop_reason"] = "repeated-page"
                return result
            page_signatures.add(signature)
            if any(parse_ui_date(str(row.get("last_modified") or "")) is None for row in data["rows"]):
                result["stop_reason"] = "unparsed-last-modified"
                return result
            if page_has_too_new:
                result.update(scan_complete=True, stop_reason="date-boundary")
                return result
            if _at_first_page(data):
                result.update(scan_complete=True, stop_reason="first-page")
                return result
            if _page_number(data) == 1:
                result["stop_reason"] = "first-page-unverified"
                return result
            if page_index + 1 == MAX_PAGES:
                result["stop_reason"] = "max-pages"
                return result
            data, problem = _read_previous_page(store_id, data)
            if problem:
                result["stop_reason"] = problem
                return result
    except Exception as error:
        logger.warning("Target invitation scan stopped: %s", error)
        result["stop_reason"] = "scan-error"
    return result


def _validate_fixed_target(invitation_id: str, expected_last_modified: str, cutoff: date) -> None:
    _validate_cutoff(cutoff)
    if not isinstance(invitation_id, str) or not invitation_id.strip():
        raise ValueError("A fixed nonempty invitation_id is required")
    if not isinstance(expected_last_modified, str):
        raise ValueError("The frozen last_modified must be a string")
    expected_date = parse_ui_date(expected_last_modified)
    if expected_date is None or expected_date >= cutoff:
        raise ValueError("The frozen target date must be parseable and strictly before cutoff")


def _revalidate_row(row: Mapping[str, Any], expected_last_modified: str, cutoff: date) -> str:
    if str(row.get("last_modified") or "").strip() != expected_last_modified.strip():
        return "last-modified-changed"
    eligible, reason = evaluate_invitation(row, cutoff=cutoff)
    return "" if eligible else reason


def locate_target_invitation(
    store_id: str, *, invitation_id: str, expected_last_modified: str, cutoff: date,
) -> InvitationLocationResult:
    """Locate exactly one immutable target, without returning/discovering others."""
    _validate_fixed_target(invitation_id, expected_last_modified, cutoff)
    result: InvitationLocationResult = {"found": False, "revalidated": False, "reason": "", "row": None, "pages_scanned": 0}
    signatures: set[tuple[str, ...]] = set()
    try:
        restored = restore_ongoing_list(store_id)
        if not isinstance(restored.get("nav"), dict) or restored["nav"].get("ok") is not True:
            result["reason"] = "last-page-navigation-failed"
            return result
        zclaw_exec(store_id, PREPARE_LIST_JS)
        data = extract_page(store_id)
        context = _shop_context(str(data.get("href") or ""))
        for page_index in range(MAX_PAGES):
            result["pages_scanned"] += 1
            problem = _page_problem(data, context)
            if problem:
                result["reason"] = problem
                return result
            if not data["rows"]:
                result["reason"] = "not-found"
                return result
            if page_index == 0 and not _at_last_page(data):
                result["reason"] = "last-page-unverified"
                return result
            signature = tuple(row_identity(row) for row in data["rows"])
            if signature in signatures:
                result["reason"] = "repeated-page"
                return result
            signatures.add(signature)
            matches = [row for row in data["rows"] if str(row.get("invitation_id") or "") == invitation_id]
            if matches:
                row = dict(matches[0])
                problem = _revalidate_row(row, expected_last_modified, cutoff)
                result.update(found=True, revalidated=not problem, reason=problem or "located", row=row)
                return result
            if _at_first_page(data):
                result["reason"] = "not-found"
                return result
            if _page_number(data) == 1:
                result["reason"] = "first-page-unverified"
                return result
            if page_index + 1 == MAX_PAGES:
                result["reason"] = "max-pages"
                return result
            data, problem = _read_previous_page(store_id, data)
            if problem:
                result["reason"] = problem
                return result
    except Exception as error:
        logger.warning("Fixed invitation locator stopped: %s", error)
        result["reason"] = "location-error"
    return result


def _wait_invitation_gone(store_id: str, invitation_id: str, previous: dict[str, Any]) -> bool | None:
    """Absence only on the same ongoing page, never after tab/page replacement."""
    context = _shop_context(str(previous.get("href") or ""))
    for poll_number in range(10):
        state = extract_page(store_id)
        if _page_problem(state, context) or _page_number(state) != _page_number(previous):
            return None
        if any(not str(row.get("invitation_id") or "") for row in state["rows"]):
            return None
        if not any(str(row["invitation_id"]) == invitation_id for row in state["rows"]):
            return True
        if poll_number < 9:
            time.sleep(0.5)
    return False


def cancel_invitation_by_id(
    store_id: str, *, invitation_id: str, expected_last_modified: str, cutoff: date, execute: bool = False,
) -> InvitationCancellationResult:
    """One write at most, after caller checkpoint; broad source ok is not success.

    ``submitted`` means click + confirmation + same-page disappearance evidence,
    not platform-final cancellation. Missing evidence/transport loss/recovery
    failure is ``uncertain`` and must never be automatically retried by callers.
    The default performs only read checks, without opening a cancellation menu.
    """
    _validate_fixed_target(invitation_id, expected_last_modified, cutoff)
    if type(execute) is not bool:
        raise ValueError("execute must be an explicit boolean")
    result: InvitationCancellationResult = {
        "status": "failed", "action": "not-attempted", "confirmed": None, "gone": None,
        "write_attempted": False, "reason": "", "raw": None,
    }
    try:
        data = extract_page(store_id)
        problem = _page_problem(data, None)
        matches = [row for row in data["rows"] if str(row.get("invitation_id") or "") == invitation_id]
        if not problem and len(matches) != 1:
            problem = "duplicate-invitation-id" if matches else "row-not-found"
        if not problem:
            problem = _revalidate_row(matches[0], expected_last_modified, cutoff)
        if problem:
            result.update(status="skipped", reason=problem)
            return result
    except Exception as error:
        result["reason"] = "prewrite-read-failed"
        result["raw"] = {"error": str(error)}
        return result
    if not execute:
        result.update(status="dry-run", action="dry-run", reason="execute-disabled")
        return result
    replacements = {placeholder: json.dumps(value, ensure_ascii=False) for placeholder, value in {
        "%INVITATION_ID%": invitation_id, "%EXPECTED_MODIFIED%": expected_last_modified.strip(),
        "%EXPECTED_HREF%": str(data["href"]), "%EXPECTED_PAGE%": str(data["page"]),
    }.items()}
    replacements["%DO_CANCEL%"] = "true"
    # One-pass substitution prevents a placeholder inside an ID from becoming
    # executable template syntax during a later replacement.
    script = re.sub(r"%(?:INVITATION_ID|EXPECTED_MODIFIED|EXPECTED_HREF|EXPECTED_PAGE|DO_CANCEL)%",
                    lambda match: replacements[match.group()], CANCEL_INVITE_JS_TMPL)
    result.update(write_attempted=True, status="uncertain", action="cancel:unknown")
    try:
        # Explicitly disable transport retries for this side-effectful call.
        raw = zclaw_exec(store_id, script, retries=0, retry_timeout_expired=False)
        result["raw"] = raw
        result["action"] = "cancel:" + json.dumps(raw, ensure_ascii=False, sort_keys=True)
        if not isinstance(raw, dict) or raw.get("invitation_id") != invitation_id:
            result["reason"] = "invalid-write-response"
        elif raw.get("write_attempted") is False and raw.get("ok") is False:
            result.update(write_attempted=False, status="failed", reason=str(raw.get("reason") or "prewrite-rejected"))
        else:
            result["confirmed"] = raw.get("confirmed") if type(raw.get("confirmed")) is bool else None
            result["gone"] = _wait_invitation_gone(store_id, invitation_id, data)
            if (raw.get("ok") is True and raw.get("clickedCancel") is True
                    and result["confirmed"] is True and result["gone"] is True):
                result.update(status="submitted", reason="operation-submitted")
            else:
                result["reason"] = "cancellation-unverified"
    except Exception as error:
        result.update(status="uncertain", reason="write-or-readback-error")
        result["raw"] = {"response": result["raw"], "error": str(error)}
    finally:
        try:
            restored = restore_ongoing_list(store_id)
            if not isinstance(restored.get("nav"), dict) or restored["nav"].get("ok") is not True:
                raise RuntimeError("Last-page recovery failed")
        except Exception as error:
            if result["write_attempted"]:
                result.update(status="uncertain", reason="postwrite-recovery-failed")
            logger.warning("Post-cancellation recovery failed: %s", error)
        time.sleep(CANCEL_DELAY_SECONDS)
    return result
