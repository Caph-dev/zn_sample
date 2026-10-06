"""Bounded target-invitation DOM operations using the existing ZClaw transport.

Origin: zn_daren/scripts/lib/zclaw_dom.py at 2d8e21c plus its 2026-10-05
worktree date-regex change accepting optional English "on". Selectors, date
extraction, reverse pagination and recovery order come from that source. Unlike
its broad cancel success/retry behavior, this migration has no write retry block.
Missing pagination evidence fails closed instead of inventing a selector.

The caller owns store/shop binding, immutable candidate validation and the
write-ahead ``attempting`` checkpoint. Locate first, persist that checkpoint,
then cancel the same fixed ID. Confirmation is optional, as in the source tool.
No candidate discovery occurs during execution.
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

from .zclaw import is_bridge_network_error, is_timeout_expired_error, zclaw_exec

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_PAGES = 50
PAGE_WAIT_SECONDS = 1.2
PAGE_SIZE_OPTION_WAIT_SECONDS = 2.5
UI_POLL_INTERVAL_SECONDS = 0.25
CANCELLATION_MENU_WAIT_SECONDS = 3.0
CANCELLATION_CONFIRMATION_WAIT_SECONDS = 4.0


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
    return JSON.stringify({ok:true, already:true, current, href:location.href});
  }
  const trigger = document.querySelector('.core-pagination-option .core-select-view')
    || document.querySelector('.core-pagination-option .core-select')
    || document.querySelector('.core-pagination-option');
  if (!trigger) return JSON.stringify({ok:false, reason:'no-pagesize-control', current});
  trigger.click();
  return JSON.stringify({ok:true, opened:true, current, href:location.href});
})()
"""

PAGE_SIZE_OPTION_HELPERS_JS = r"""
  const expectedHref = %EXPECTED_HREF%;
  if (location.href !== expectedHref) return JSON.stringify({ok:false, reason:'pagesize-context-changed'});
  const options = [...document.querySelectorAll('li[role=option], .core-select-option, div[role=option], .arco-select-option, li')]
    .filter(element => {
      const text = (element.innerText || '').trim();
      const rectangle = element.getBoundingClientRect();
      return (text === '100/页' || text === '100' || text === '100 / 页')
        && rectangle.width > 0 && rectangle.height > 0;
    });
  if (options.length > 1) return JSON.stringify({ok:false, reason:'ambiguous-pagesize-option'});
"""

INSPECT_PAGE_SIZE_OPTION_JS_TMPL = "(() => {\n" + PAGE_SIZE_OPTION_HELPERS_JS + r"""
  return JSON.stringify({ok:true, ready:options.length === 1});
})()
"""

CLICK_PAGE_SIZE_OPTION_JS_TMPL = "(() => {\n" + PAGE_SIZE_OPTION_HELPERS_JS + r"""
  if (options.length !== 1) return JSON.stringify({ok:false, reason:'option-not-found'});
  options[0].click();
  return JSON.stringify({ok:true, set:true});
})()
"""

INSPECT_PAGE_SIZE_JS_TMPL = r"""
(() => {
  if (location.href !== %EXPECTED_HREF%) return JSON.stringify({ok:false, reason:'pagesize-context-changed'});
  const valueElement = document.querySelector('.core-pagination-option .core-select-view-value');
  const current = (valueElement && valueElement.innerText || '').trim();
  return JSON.stringify({ok:true, ready:current === '100/页' || current === '100' || current === '100 / 页', current});
})()
"""

OPEN_CANCELLATION_MENU_JS_TMPL = "(() => {\n" + INVITATION_DOM_HELPERS_JS + r"""
  const targetId = %INVITATION_ID%;
  const expectedModified = %EXPECTED_MODIFIED%;
  if (location.href !== %EXPECTED_HREF%) return JSON.stringify({ok:false, reason:'list-context-changed'});
  const matches = [...document.querySelectorAll('table tbody tr')].filter(tableRow => {
    const record = getRecord(tableRow);
    return record && String(record.id) === targetId;
  });
  if (matches.length !== 1) return JSON.stringify({ok:false, reason:'row-not-found'});
  const tableRow = matches[0];
  if (extractRow(tableRow, 0).last_modified !== expectedModified) {
    return JSON.stringify({ok:false, reason:'last-modified-changed'});
  }
  const operationCell = [...tableRow.querySelectorAll('td')].pop();
  const buttons = operationCell ? [...operationCell.querySelectorAll('button')] : [];
  const arrow = buttons.find(button => !(button.innerText || '').trim()
    || /[∨▼]/.test(button.innerText || '')) || buttons[1];
  if (!arrow) return JSON.stringify({ok:false, reason:'no-arrow'});
  tableRow.scrollIntoView({block:'center', inline:'nearest'});
  window.__znSampleCleanupAttempt = {
    invitationId:targetId, buttons:new Set(document.querySelectorAll('button'))
  };
  arrow.click();
  return JSON.stringify({ok:true, opened:true});
})()
"""

CANCELLATION_MENU_HELPERS_JS = INVITATION_DOM_HELPERS_JS + r"""
  const targetId = %INVITATION_ID%;
  const attempt = window.__znSampleCleanupAttempt;
  if (!attempt || attempt.invitationId !== targetId || location.href !== %EXPECTED_HREF%) {
    return JSON.stringify({ok:false, reason:'list-context-changed', write_attempted:false});
  }
  const menuItems = [...document.querySelectorAll('[role=menuitem]')].filter(element => {
    const rectangle = element.getBoundingClientRect();
    const record = getRecord(element);
    return rectangle.width > 0 && rectangle.height > 0
      && /^(取消邀请|Cancel invitation)$/i.test((element.innerText || '').trim())
      && record && String(record.id) === targetId;
  });
  if (menuItems.length > 1) return JSON.stringify({ok:false, reason:'ambiguous-cancellation-menu', write_attempted:false});
"""

INSPECT_CANCELLATION_MENU_JS_TMPL = "(() => {\n" + CANCELLATION_MENU_HELPERS_JS + r"""
  return JSON.stringify({ok:true, ready:menuItems.length === 1});
})()
"""

CLICK_CANCELLATION_MENU_JS_TMPL = "(() => {\n" + CANCELLATION_MENU_HELPERS_JS + r"""
  if (menuItems.length !== 1) return JSON.stringify({ok:false, reason:'menu-item-not-found', write_attempted:false});
  menuItems[0].click();
  return JSON.stringify({ok:true, invitation_id:targetId, clickedCancel:true});
})()
"""

CANCELLATION_CONFIRMATION_HELPERS_JS = r"""
  const attempt = window.__znSampleCleanupAttempt;
  if (!attempt || attempt.invitationId !== %INVITATION_ID%) {
    return JSON.stringify({ok:false, reason:'cancellation-attempt-missing'});
  }
  const confirmations = [...document.querySelectorAll('button')].filter(button => {
    const rectangle = button.getBoundingClientRect();
    if (attempt.buttons.has(button) || rectangle.width <= 0 || rectangle.height <= 0) return false;
    const text = (button.innerText || '').trim();
    const fiberKey = Object.keys(button).find(property => property.startsWith('__reactFiber')
      || property.startsWith('__reactInternalInstance'));
    let fiber = fiberKey ? button[fiberKey] : null;
    for (let depth = 0; depth < 40 && fiber; depth++, fiber = fiber.return) {
      const properties = fiber.memoizedProps;
      if (properties && typeof properties.onOk === 'function' && properties.okText != null
        && properties.cancelText != null && properties.title != null) {
        return text === String(properties.okText) && text !== String(properties.cancelText);
      }
    }
    return false;
  });
  if (confirmations.length > 1) return JSON.stringify({ok:false, reason:'ambiguous-cancellation-confirmation'});
"""

INSPECT_CANCELLATION_CONFIRMATION_JS_TMPL = "(() => {\n" + CANCELLATION_CONFIRMATION_HELPERS_JS + r"""
  return JSON.stringify({ok:true, ready:confirmations.length === 1});
})()
"""

CLICK_CANCELLATION_CONFIRMATION_JS_TMPL = "(() => {\n" + CANCELLATION_CONFIRMATION_HELPERS_JS + r"""
  if (confirmations.length !== 1) return JSON.stringify({ok:false, reason:'confirmation-not-found'});
  const confirmText = (confirmations[0].innerText || '').trim();
  confirmations[0].click();
  return JSON.stringify({ok:true, confirmed:true, confirmText});
})()
"""

CLEAR_CANCELLATION_ATTEMPT_JS = r"""
(() => {
  delete window.__znSampleCleanupAttempt;
  return JSON.stringify({ok:true});
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

ONGOING_TAB_LOOKUP_JS = r"""
  const ongoingTab = [...document.querySelectorAll('.core-tabs-header-title, [role=tab]')].find(element => {
    const text = (element.innerText || '').trim().replace(/\s+/g, ' ');
    return (/^(进行中|Ongoing)\b/i.test(text) || text === '进行中' || /^进行中\s*\d+/.test(text))
      && element.getBoundingClientRect().width > 0;
  });
"""

ENSURE_ONGOING_TAB_JS = "(() => {\n" + ONGOING_TAB_LOOKUP_JS + r"""
  if (!ongoingTab) return JSON.stringify({ok:false, reason:'ongoing-tab-not-found', href:location.href});
  const already = ongoingTab.classList.contains('core-tabs-header-title-active')
    || ongoingTab.getAttribute('aria-selected') === 'true';
  if (already) return JSON.stringify({ok:true, already:true, href:location.href});
  ongoingTab.click();
  return JSON.stringify({ok:true, clicked:true, href:location.href});
})()
"""

INSPECT_ONGOING_TAB_JS = "(() => {\n" + ONGOING_TAB_LOOKUP_JS + r"""
  if (!ongoingTab) return JSON.stringify({ok:false, reason:'ongoing-tab-not-found', href:location.href});
  return JSON.stringify({ok:true, already:ongoingTab.classList.contains('core-tabs-header-title-active')
    || ongoingTab.getAttribute('aria-selected') === 'true', href:location.href});
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

RECOVERY_CONTEXT_GUARD_JS = r"""
  const expectedContext = null;
  const currentUrl = new URL(location.href);
  const shopIds = currentUrl.searchParams.getAll('shop_id');
  const regions = currentUrl.searchParams.getAll('shop_region');
  const currentContext = [currentUrl.hostname, (shopIds[0] || '').trim(), (regions[0] || '').trim().toUpperCase()];
  if (currentUrl.protocol !== 'https:' || currentUrl.hostname !== 'affiliate.tiktokshopglobalselling.com'
    || !['/affiliate/collaboration/target-invitation', '/connection/target-invitation'].includes(currentUrl.pathname.replace(/\/$/, ''))
    || shopIds.length !== 1 || regions.length !== 1 || !currentContext[1] || !currentContext[2]
    || (expectedContext && currentContext.some((value, index) => value !== expectedContext[index]))
    || /log\s*in|sign[-_]?in|passport/i.test(document.title || '')
    || /Log in to TikTok Shop|登录 TikTok Shop|Sign in to TikTok Shop/i.test(document.body && document.body.innerText || '')) {
    return JSON.stringify({ok:false, reason:'list-context-changed', href:location.href});
  }
"""


def _guard_recovery_script(script: str, context: tuple[str, str, str] | None = None) -> str:
    guard = RECOVERY_CONTEXT_GUARD_JS.replace("const expectedContext = null;",
                                            "const expectedContext = " + json.dumps(context) + ";")
    return script.replace("(() => {", "(() => {\n" + guard, 1)


# The identity check runs in the same script before any recovery side effect.
ENSURE_ONGOING_TAB_JS = _guard_recovery_script(ENSURE_ONGOING_TAB_JS)
INSPECT_ONGOING_TAB_JS = _guard_recovery_script(INSPECT_ONGOING_TAB_JS)
CLICK_LIST_RETRY_JS = _guard_recovery_script(CLICK_LIST_RETRY_JS)
INSPECT_LIST_ERROR_JS = _guard_recovery_script(INSPECT_LIST_ERROR_JS)
SET_PAGE_SIZE_JS = _guard_recovery_script(SET_PAGE_SIZE_JS)
GOTO_LAST_PAGE_JS = _guard_recovery_script(GOTO_LAST_PAGE_JS)
CLICK_PAGE_SIZE_OPTION_JS_TMPL = _guard_recovery_script(CLICK_PAGE_SIZE_OPTION_JS_TMPL)


def _bind_recovery_context(script: str, context: tuple[str, str, str] | None) -> str:
    return script.replace("const expectedContext = null;", "const expectedContext = " + json.dumps(context) + ";")


def _recovery_probe(store_id: str, script: str, *, deadline: float,
                    context: tuple[str, str, str] | None = None, execute_script_fn=None) -> dict[str, Any]:
    if deadline - time.monotonic() < 0.5:
        raise RuntimeError("List recovery deadline expired")
    execute_script = execute_script_fn or zclaw_exec
    result = execute_script(store_id, _bind_recovery_context(script, context), timeout=15,
                            deadline=deadline, retries=0, retry_timeout_expired=False)
    if time.monotonic() >= deadline:
        raise RuntimeError("List recovery deadline expired")
    if not isinstance(result, dict) or result.get("ok") is not True:
        if isinstance(result, dict) and result.get("reason") == "last-page-control-not-found":
            raise RecoveryControlUnavailable("Last-page control not found")
        raise RuntimeError(f"List recovery rejected: {result!r}")
    return result


def _recovery_sleep(seconds: float, deadline: float) -> None:
    time.sleep(min(seconds, max(0.0, deadline - time.monotonic())))


def _is_transient_read_error(error: Exception) -> bool:
    return isinstance(error, TimeoutError) or is_timeout_expired_error(error) or is_bridge_network_error(error)


def _read_recovery_state(store_id: str, script: str, *, deadline: float,
                         context=None, execute_script_fn=None, timeout: int = 15) -> dict[str, Any]:
    """Repeat only read transport failures; page rejection is never a retry signal."""
    execute_script = execute_script_fn or zclaw_exec
    while deadline - time.monotonic() >= 0.5:
        try:
            result = execute_script(store_id, _bind_recovery_context(script, context), timeout=timeout,
                                    deadline=deadline, retries=0, retry_timeout_expired=False)
        except Exception as error:
            if not _is_transient_read_error(error):
                raise
            _recovery_sleep(UI_POLL_INTERVAL_SECONDS, deadline)
            continue
        if time.monotonic() >= deadline:
            break
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError(f"List recovery read rejected: {result!r}")
        return result
    raise RuntimeError("List recovery read deadline expired")


class RecoveryControlUnavailable(RuntimeError):
    """A known missing control permits only a guarded genuine-empty-state read."""


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


def ensure_ongoing_tab(store_id: str, *, page_wait: float = PAGE_WAIT_SECONDS,
                       expected_context=None, deadline: float | None = None, execute_script_fn=None) -> dict[str, Any]:
    deadline = deadline if deadline is not None else time.monotonic() + 90.0
    result = _recovery_probe(store_id, ENSURE_ONGOING_TAB_JS, context=expected_context,
                             deadline=deadline, execute_script_fn=execute_script_fn)
    context = expected_context or _shop_context(str(result.get("href") or ""))
    if context is None:
        raise RuntimeError("Ongoing invitations context not verified")
    if result.get("clicked"):
        _recovery_sleep(page_wait, deadline)
        # Verify selection without invoking another possibly-clicking tab script.
        result = _read_recovery_state(store_id, INSPECT_ONGOING_TAB_JS, context=context,
                                     deadline=deadline, execute_script_fn=execute_script_fn)
    if not isinstance(result, dict) or result.get("already") is not True:
        raise RuntimeError(f"Ongoing invitations tab not verified: {result!r}")
    return result


def ensure_page_size(store_id: str, *, expected_context=None, deadline: float | None = None,
                     execute_script_fn=None) -> dict[str, Any]:
    overall_deadline = deadline if deadline is not None else time.monotonic() + 90.0
    deadline = min(overall_deadline, time.monotonic() + PAGE_SIZE_OPTION_WAIT_SECONDS)
    if deadline - time.monotonic() < 0.5:
        raise RuntimeError("Cannot restore 100 rows per page: deadline expired")
    execute_script = execute_script_fn or zclaw_exec
    result = execute_script(
        store_id, _bind_recovery_context(SET_PAGE_SIZE_JS, expected_context), timeout=3, deadline=deadline,
        retries=0, retry_timeout_expired=False,
    )
    if not isinstance(result, dict) or result.get("ok") is not True:
        if isinstance(result, dict) and result.get("reason") == "no-pagesize-control":
            raise RecoveryControlUnavailable("Cannot restore 100 rows per page: no-pagesize-control")
        raise RuntimeError(f"Cannot restore 100 rows per page: {result!r}")
    if result.get("already") is True:
        return result
    expected_href = str(result.get("href") or "")
    if result.get("opened") is not True or _shop_context(expected_href) is None:
        raise RuntimeError("Cannot restore 100 rows per page: invalid trigger response")
    replacements = {"%EXPECTED_HREF%": json.dumps(expected_href, ensure_ascii=False)}

    def render_script(template: str) -> str:
        script = re.sub(r"%EXPECTED_HREF%", lambda match: replacements[match.group()], template)
        return _bind_recovery_context(script, expected_context or _shop_context(expected_href))

    _wait_for_ui_ready(store_id, render_script(INSPECT_PAGE_SIZE_OPTION_JS_TMPL), deadline=deadline,
                       execute_script_fn=execute_script)
    if deadline - time.monotonic() < 0.5:
        raise RuntimeError("Cannot restore 100 rows per page: option deadline expired")
    selected = execute_script(
        store_id, render_script(CLICK_PAGE_SIZE_OPTION_JS_TMPL), timeout=3, deadline=deadline,
        retries=0, retry_timeout_expired=False,
    )
    if not isinstance(selected, dict) or selected.get("ok") is not True or selected.get("set") is not True:
        raise RuntimeError(f"Cannot restore 100 rows per page: option click unverified: {selected!r}")
    verified = _wait_for_ui_ready(
        store_id, render_script(INSPECT_PAGE_SIZE_JS_TMPL),
        deadline=min(overall_deadline, time.monotonic() + PAGE_WAIT_SECONDS), execute_script_fn=execute_script,
    )
    return {"ok": True, "set": True, "previous": result.get("current"), "current": verified["current"]}


def _wait_for_ui_ready(
    store_id: str, script: str, *, deadline: float, required: bool = True, execute_script_fn=None,
) -> dict[str, Any]:
    """Only read probes repeat; neither blocking probes nor sleeps extend the budget."""
    # The existing transport needs at least half a second and rounds to whole seconds.
    while deadline - time.monotonic() >= 0.5:
        state = _read_recovery_state(store_id, script, timeout=3, deadline=deadline,
                                     execute_script_fn=execute_script_fn)
        if type(state.get("ready")) is not bool:
            raise RuntimeError(f"UI readiness rejected: {state!r}")
        if time.monotonic() >= deadline:
            break
        if state.get("ready") is True:
            return state
        time.sleep(min(UI_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    if required:
        raise RuntimeError("UI readiness deadline expired")
    return {"ok": True, "ready": False}


def recover_list_if_error(store_id: str, *, expected_context=None, deadline: float | None = None,
                          execute_script_fn=None) -> dict[str, Any]:
    """Only the source's observed list-error Retry action; never reopen a store."""
    deadline = deadline if deadline is not None else time.monotonic() + 90.0
    for attempt in range(4):
        state = _read_recovery_state(store_id, INSPECT_LIST_ERROR_JS, context=expected_context,
                                     deadline=deadline, execute_script_fn=execute_script_fn)
        if type(state.get("error")) is not bool or type(state.get("can_retry")) is not bool:
            raise RuntimeError(f"Malformed list error state: {state!r}")
        expected_context = expected_context or _shop_context(str(state.get("href") or ""))
        if expected_context is None:
            raise RuntimeError("List recovery context not verified")
        if state.get("error") is False:
            return {"ok": True, "needed": attempt > 0, "state": state}
        if attempt == 3 or state.get("can_retry") is not True:
            raise RuntimeError(f"List error not recovered: {state!r}")
        logger.warning("Recovering target invitation list error (%s/3)", attempt + 1)
        clicked = _recovery_probe(store_id, CLICK_LIST_RETRY_JS, context=expected_context,
                                  deadline=deadline, execute_script_fn=execute_script_fn)
        if clicked.get("clicked") is not True:
            raise RuntimeError("List retry action response unverified")
        _recovery_sleep(2.5 * (attempt + 1), deadline)
    raise AssertionError("Unreachable recovery state")


def restore_ongoing_list(store_id: str, *, expected_context=None, deadline: float | None = None,
                          execute_script_fn=None, ensure_ongoing_fn=None, wait_ready_fn=None,
                          wait_shell_fn=None) -> dict[str, Any]:
    """Required order: ongoing, list recovery, fixed 100/page, last page."""
    from .target_invitation_navigation import wait_for_target_list_ready, wait_for_target_shell

    deadline = deadline if deadline is not None else time.monotonic() + 90.0
    execute_script = execute_script_fn or zclaw_exec
    shell = (wait_shell_fn or wait_for_target_shell)(store_id, deadline=deadline,
              expected_context=expected_context, execute_script_fn=execute_script)
    expected_context = expected_context or _shop_context(str(shell.get("href") or ""))
    if expected_context is None:
        raise RuntimeError("List recovery context not verified")
    tab = (ensure_ongoing_fn or ensure_ongoing_tab)(store_id, expected_context=expected_context,
              deadline=deadline, execute_script_fn=execute_script)
    recovered = recover_list_if_error(store_id, expected_context=expected_context,
                 deadline=deadline, execute_script_fn=execute_script)
    wait_ready = wait_ready_fn or wait_for_target_list_ready
    readiness_options = {"deadline": deadline, "expected_context": expected_context,
                         "require_ongoing": True, "execute_script_fn": execute_script}
    ready = wait_ready(store_id, **readiness_options)
    if ready.get("ready") is not True:
        raise RuntimeError("Target invitation list readiness not verified")
    if _shop_context(str(ready.get("href") or "")) != expected_context:
        raise RuntimeError("Platform shop changed while waiting for target list")
    try:
        paging = ensure_page_size(store_id, expected_context=expected_context,
                                  deadline=deadline, execute_script_fn=execute_script)
    except RecoveryControlUnavailable:
        # Do not replay a trigger or option click after an unverified response.
        if time.monotonic() >= deadline:
            raise
        empty_state = _read_recovery_state(store_id, _guard_recovery_script(EXTRACT_JS),
                        context=expected_context, deadline=deadline, execute_script_fn=execute_script)
        if not empty_state["rows"] and not _page_problem(empty_state, expected_context):
            return {"tab": tab, "recovered": recovered, "paging": {"ok": True, "empty": True},
                    "nav": {"ok": True, "empty": True}}
        raise
    # Single action receipt; only the observation below may repeat.
    try:
        navigation = _recovery_probe(store_id, GOTO_LAST_PAGE_JS, context=expected_context,
                                      deadline=deadline, execute_script_fn=execute_script)
    except RecoveryControlUnavailable:
        empty_state = _read_recovery_state(store_id, _guard_recovery_script(EXTRACT_JS),
                       context=expected_context, deadline=deadline, execute_script_fn=execute_script)
        if not isinstance(empty_state.get("rows"), list) or empty_state["rows"] or _page_problem(empty_state, expected_context):
            raise RuntimeError("Missing last-page control without verified empty list")
        return {"tab": tab, "recovered": recovered, "paging": paging, "nav": {"ok": True, "empty": True}}
    if navigation.get("already") is not True and navigation.get("via") not in {"last-btn", "max-page-item"}:
        raise RuntimeError("Last-page action response unverified")
    observed = _wait_for_last_page(store_id, expected_context=expected_context,
                                   deadline=deadline, execute_script_fn=execute_script)
    navigation = {**navigation, "observed_page": observed.get("page"), "empty": not observed["rows"]}
    final_ready = wait_ready(store_id, **readiness_options)
    if final_ready.get("ready") is not True:
        raise RuntimeError("Target invitation list readiness not verified")
    if _shop_context(str(final_ready.get("href") or "")) != expected_context:
        raise RuntimeError("Platform shop changed while waiting for target list")
    return {"tab": tab, "recovered": recovered, "paging": paging, "nav": navigation}


def _wait_for_last_page(store_id: str, *, expected_context, deadline: float,
                        execute_script_fn=None) -> dict[str, Any]:
    last_problem = "last-page-unverified"
    while deadline - time.monotonic() >= 0.5:
        state = _read_recovery_state(store_id, _guard_recovery_script(EXTRACT_JS),
                                     context=expected_context, deadline=deadline, execute_script_fn=execute_script_fn)
        if not isinstance(state.get("rows"), list) or any(not isinstance(row, dict) for row in state["rows"]):
            raise RuntimeError("Malformed last-page observation")
        last_problem = _page_problem(state, expected_context)
        if last_problem in {"list-context-changed", "page-total-unverified", "duplicate-invitation-id", "page-size-exceeded"}:
            raise RuntimeError(f"Last-page observation rejected: {last_problem}")
        if not last_problem and (not state["rows"] or _at_last_page(state)):
            return state
        _recovery_sleep(UI_POLL_INTERVAL_SECONDS, deadline)
    raise RuntimeError(f"Last-page observation timed out: {last_problem or 'last-page-unverified'}")


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
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
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
    total_text = str(data.get("total", ""))
    # Pagination controls alone do not prove that every virtual row is mounted.
    if not re.fullmatch(r"[0-9]+", total_text):
        return "page-total-unverified"
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


def cancel_invitation_by_id(
    store_id: str, *, invitation_id: str, expected_last_modified: str, cutoff: date, execute: bool = False,
) -> InvitationCancellationResult:
    """Submit one cancellation, with optional confirmation as in the source tool.

    A click receipt means operation-submitted, not platform-final cancellation.
    Transport loss after calling cancellation remains uncertain and is never
    retried. The caller retains the existing snapshot and write-ahead barriers.
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
    replacements = {
        "%INVITATION_ID%": json.dumps(invitation_id, ensure_ascii=False),
        "%EXPECTED_MODIFIED%": json.dumps(expected_last_modified.strip(), ensure_ascii=False),
        "%EXPECTED_HREF%": json.dumps(str(data["href"]), ensure_ascii=False),
    }

    def render_script(template: str) -> str:
        return re.sub(r"%(?:INVITATION_ID|EXPECTED_MODIFIED|EXPECTED_HREF)%",
                      lambda match: replacements[match.group()], template)

    def execute_once(template: str) -> dict[str, Any]:
        response = zclaw_exec(store_id, render_script(template), retries=0, retry_timeout_expired=False)
        if not isinstance(response, dict):
            raise RuntimeError("Invalid cancellation response")
        return response

    menu_opened = False
    try:
        # A lost arrow-click response may still leave the menu open.
        menu_opened = True
        opened = execute_once(OPEN_CANCELLATION_MENU_JS_TMPL)
        menu_opened = opened.get("opened") is True
        if opened.get("ok") is not True:
            result.update(reason=str(opened.get("reason") or "menu-open-failed"), raw=opened)
            return result
        _wait_for_ui_ready(
            store_id, render_script(INSPECT_CANCELLATION_MENU_JS_TMPL),
            deadline=time.monotonic() + CANCELLATION_MENU_WAIT_SECONDS,
        )
        result.update(write_attempted=True, status="uncertain", action="cancel:unknown")
        cancellation = execute_once(CLICK_CANCELLATION_MENU_JS_TMPL)
        result["raw"] = cancellation
        if cancellation.get("write_attempted") is False:
            result.update(status="failed", write_attempted=False, action="not-attempted",
                          reason=str(cancellation.get("reason") or "menu-item-not-found"))
            return result
        if cancellation.get("ok") is not True or cancellation.get("invitation_id") != invitation_id \
                or cancellation.get("clickedCancel") is not True:
            result["reason"] = "invalid-write-response"
            return result

        confirmation_ready = _wait_for_ui_ready(
            store_id, render_script(INSPECT_CANCELLATION_CONFIRMATION_JS_TMPL),
            deadline=time.monotonic() + CANCELLATION_CONFIRMATION_WAIT_SECONDS, required=False,
        )
        confirmation = {"confirmed": False}
        if confirmation_ready["ready"]:
            confirmation = execute_once(CLICK_CANCELLATION_CONFIRMATION_JS_TMPL)
            if confirmation.get("ok") is not True or confirmation.get("confirmed") is not True:
                result.update(reason="confirmation-unverified", raw={**cancellation, **confirmation})
                return result
        result.update(status="submitted", reason="operation-submitted",
                      confirmed=confirmation["confirmed"], raw={**cancellation, **confirmation})
        result["action"] = "cancel:" + json.dumps(result["raw"], ensure_ascii=False, sort_keys=True)
    except Exception as error:
        result["reason"] = "write-or-readback-error" if result["write_attempted"] else "menu-open-failed"
        result["raw"] = {"response": result["raw"], "error": str(error)}
        logger.warning("Invitation cancellation stopped: %s", error)
    finally:
        try:
            zclaw_exec(store_id, CLEAR_CANCELLATION_ATTEMPT_JS, retries=0, retry_timeout_expired=False)
        except Exception as error:
            logger.warning("Cannot clear cancellation UI state: %s", error)
        if menu_opened or result["write_attempted"]:
            try:
                restored = restore_ongoing_list(store_id, expected_context=_shop_context(str(data["href"])))
                if restored.get("nav", {}).get("ok") is not True:
                    raise RuntimeError("Last-page recovery failed")
            except Exception as error:
                if result["write_attempted"]:
                    result.update(status="uncertain", reason="postwrite-recovery-failed")
                logger.warning("Post-cancellation recovery failed: %s", error)
    return result
