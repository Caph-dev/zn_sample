#!/usr/bin/env python3
"""样品申请「已完成」列表：切 tab、在整表定位达人、只读打开「查看内容」。

页面「达人昵称」搜索框实测会把表格刷成「列表为空」，即使列表 API
``search_key=1`` 能唯一命中。打开抽屉改为：重置筛选后翻页找行。

不点同意 / 拒绝 / 发货 / 发送。
"""
from __future__ import annotations

import json
import time
from typing import Any

from .sample_dom import click_next
from .zclaw import zclaw_exec

ENSURE_COMPLETED_TAB_JS = r"""
(() => {
  const text = document.body.innerText || '';
  const onSample = /样品申请|Sample Requests|sample-request/i.test(location.href + text);
  if (!onSample) {
    return JSON.stringify({
      ok: false,
      reason: 'not-on-sample-page',
      href: location.href,
      hint: '请先打开：联盟中心 → 样品申请 → 免费样品'
    });
  }
  const titles = [...document.querySelectorAll('.core-tabs-header-title, [role=tab]')];
  const active = titles.find(el =>
    el.classList && el.classList.contains('core-tabs-header-title-active')
    && /已完成|Completed/i.test((el.innerText || '').trim())
  );
  if (active) {
    return JSON.stringify({
      ok: true,
      already: true,
      text: (active.innerText || '').trim().slice(0, 40),
      href: location.href
    });
  }
  const tab = titles.find(el => /已完成|Completed/i.test((el.innerText || '').trim()));
  if (!tab) {
    return JSON.stringify({
      ok: false,
      reason: 'no-completed-tab',
      href: location.href,
      titles: titles.map(el => ((el.innerText || '').trim().replace(/\s+/g, ' ')).slice(0, 40)),
    });
  }
  tab.click();
  return JSON.stringify({
    ok: true,
    clicked: true,
    text: (tab.innerText || '').trim().slice(0, 40),
    href: location.href
  });
})()
"""

SELECT_CREATOR_NICKNAME_JS = r"""
(() => {
  const hidden = [...document.querySelectorAll('input.core-select-view-input')].find(el =>
    /商品名称|达人昵称|Creator/.test(String(el.value || ''))
  );
  if (!hidden) {
    return JSON.stringify({ok: false, reason: 'no-search-type-select'});
  }
  const current = String(hidden.value || '').trim();
  if (current === '达人昵称' || /creator nickname/i.test(current)) {
    return JSON.stringify({ok: true, already: true, current});
  }
  const trigger = hidden.closest('.core-select-view') || hidden.parentElement;
  if (!trigger) {
    return JSON.stringify({ok: false, reason: 'no-search-type-trigger', current});
  }
  trigger.click();
  const option = [...document.querySelectorAll('[role="option"]')].find(el => {
    const text = (el.innerText || '').trim();
    return text === '达人昵称' || /creator nickname/i.test(text);
  });
  if (!option) {
    return JSON.stringify({ok: false, reason: 'no-creator-nickname-option', current});
  }
  option.click();
  return JSON.stringify({ok: true, clicked: true, current: '达人昵称'});
})()
"""

SEARCH_CREATOR_JS_TMPL = r"""
((name) => {
  const input = [...document.querySelectorAll('input.core-input')].find(el =>
    /搜索达人昵称|搜索商品名称|搜索达人|Search creator/i.test(String(el.placeholder || ''))
  ) || document.querySelector('input.core-input');
  if (!input) return JSON.stringify({ok: false, reason: 'no-search'});
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  input.focus();
  const tracker = input._valueTracker;
  if (tracker) tracker.setValue('');
  setter.call(input, name);
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  const fiberKey = Object.keys(input).find(key =>
    key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
  );
  let fiber = fiberKey ? input[fiberKey] : null;
  for (let depth = 0; depth < 12 && fiber; depth++, fiber = fiber.return) {
    const props = fiber.memoizedProps || {};
    if (typeof props.onPressEnter === 'function') {
      props.onPressEnter({
        key: 'Enter',
        keyCode: 13,
        which: 13,
        preventDefault() {},
        stopPropagation() {},
        target: input,
        currentTarget: input,
      });
      return JSON.stringify({
        ok: true,
        via: 'onPressEnter',
        value: input.value,
        placeholder: String(input.placeholder || ''),
      });
    }
    if (typeof props.onSearch === 'function') {
      props.onSearch(name);
      return JSON.stringify({
        ok: true,
        via: 'onSearch',
        value: input.value,
        placeholder: String(input.placeholder || ''),
      });
    }
  }
  input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
  return JSON.stringify({
    ok: true,
    via: 'keydown',
    value: input.value,
    placeholder: String(input.placeholder || ''),
  });
})(%NAME%)
"""

OPEN_VIEW_CONTENT_JS_TMPL = r"""
((creatorName) => {
  const want = String(creatorName || '').trim().toLowerCase();
  const rows = [...document.querySelectorAll('table tbody tr')];
  let matched = null;
  for (const tr of rows) {
    const rowText = String(tr.innerText || '').toLowerCase();
    if (/列表为空|暂无数据|no data/i.test(rowText)) continue;
    if (want && rowText.includes(want)) {
      matched = tr;
      break;
    }
  }
  if (!matched) {
    const dataRows = rows.filter(tr => /查看内容/.test(tr.innerText || '') && !/列表为空/.test(tr.innerText || ''));
    if (dataRows.length === 1) matched = dataRows[0];
  }
  if (!matched) {
    return JSON.stringify({ok: false, reason: 'row-not-found', href: location.href, row_count: rows.length});
  }
  const button = [...matched.querySelectorAll('span, a, button')].find(el =>
    (el.innerText || '').trim() === '查看内容'
    || (el.innerText || '').trim() === 'View content'
  );
  if (!button) {
    return JSON.stringify({
      ok: false,
      reason: 'no-view-content-button',
      href: location.href,
      row_text: String(matched.innerText || '').slice(0, 200),
    });
  }
  const fiberKey = Object.keys(button).find(key =>
    key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
  );
  let fiber = fiberKey ? button[fiberKey] : null;
  for (let depth = 0; depth < 16 && fiber; depth++, fiber = fiber.return) {
    const props = fiber.memoizedProps || {};
    if (typeof props.handleClick === 'function') {
      props.handleClick();
      return JSON.stringify({
        ok: true,
        via: 'handleClick',
        depth,
        href: location.href,
        button_text: String(button.innerText || '').trim().slice(0, 40),
      });
    }
    if (typeof props.onClick === 'function') {
      props.onClick({
        currentTarget: button,
        target: button,
        preventDefault() {},
        stopPropagation() {},
      });
      return JSON.stringify({
        ok: true,
        via: 'onClick',
        depth,
        href: location.href,
        button_text: String(button.innerText || '').trim().slice(0, 40),
      });
    }
  }
  button.click();
  return JSON.stringify({
    ok: true,
    via: 'native',
    href: location.href,
    button_text: String(button.innerText || '').trim().slice(0, 40),
  });
})(%NAME%)
"""

INSPECT_CONTENT_PANEL_JS = r"""
(() => {
  const drawer = document.querySelector('.core-drawer, .pulse-drawer, .arco-drawer');
  const dialog = document.querySelector('.core-modal, .arco-modal, [role="dialog"]');
  const root = drawer || dialog;
  if (!root) {
    return JSON.stringify({
      ok: false,
      reason: 'no-content-drawer',
      href: location.href,
      panel_text: '',
      links: [],
      has_panel: false,
    });
  }
  const text = String(root.innerText || '').replace(/\u00a0/g, ' ').trim();
  const links = [...root.querySelectorAll('a[href]')]
    .map(el => String(el.href || '').trim())
    .filter(href => href && !href.startsWith('chrome-extension:'));
  const counts = String(text).replace(/\s+/g, ' ').match(/视频\s+(\d+)\s+直播\s+(\d+)/)
    || String(text).replace(/\s+/g, ' ').match(/Video\s+(\d+)\s+Live\s+(\d+)/i);
  return JSON.stringify({
    ok: true,
    href: location.href,
    has_panel: true,
    panel_kind: drawer ? 'drawer' : 'dialog',
    panel_text: text.slice(0, 4000),
    links: links.slice(0, 20),
    video_count: counts ? Number(counts[1]) : null,
    live_count: counts ? Number(counts[2]) : null,
  });
})()
"""


def _js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def ensure_completed_tab(store_id: str, *, page_wait: float = 2.0, retries: int = 4) -> dict[str, Any]:
    """切到免费样品「已完成」tab；找不到则报错，不猜其它页签。"""
    attempts = max(1, int(retries) + 1)
    last_result: dict[str, Any] | Any = {}
    for attempt in range(attempts):
        last_result = zclaw_exec(store_id, ENSURE_COMPLETED_TAB_JS)
        if isinstance(last_result, dict) and last_result.get("ok"):
            if last_result.get("clicked"):
                time.sleep(page_wait)
            return last_result
        page_is_still_loading = bool(
            isinstance(last_result, dict)
            and last_result.get("reason") == "no-completed-tab"
            and "sample-request" in str(last_result.get("href") or "")
        )
        if page_is_still_loading and attempt + 1 < attempts:
            time.sleep(max(1.0, page_wait))
            continue
        break
    raise RuntimeError(f"无法切到「已完成」tab: {last_result}")


LIST_VISIBLE_CREATORS_JS = r"""
(() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const names = [];
  for (const tr of rows) {
    const text = String(tr.innerText || '');
    if (/列表为空|暂无数据|no data/i.test(text) || !/查看内容/.test(text)) continue;
    const recKey = Object.keys(tr).find(key =>
      key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
    );
    let record = null;
    if (recKey) {
      let fiber = tr[recKey];
      for (let depth = 0; depth < 40 && fiber; depth++, fiber = fiber.return) {
        const props = fiber.memoizedProps || {};
        if (props.record) {
          record = props.record;
          break;
        }
      }
    }
    const creator = (record && record.creator_info) || {};
    const name = String((creator && creator.name) || (record && record.name) || '').trim();
    const nick = String((creator && creator.nick_name) || (record && record.nick_name) || '').trim();
    if (name || nick) names.push({name, nick});
  }
  const next = document.querySelector(
    '.core-pagination-item-next:not(.core-pagination-item-disabled), ' +
    '.arco-pagination-item-next:not(.arco-pagination-item-disabled), ' +
    'li[aria-label="下一页"]:not([aria-disabled="true"]), ' +
    'button[aria-label="下一页"]:not([disabled])'
  );
  const active = document.querySelector('.core-pagination-item-active, .arco-pagination-item-active');
  return JSON.stringify({
    ok: true,
    page: active ? String(active.innerText || '').trim() : '',
    names,
    can_next: !!next,
    row_count: rows.length,
  });
})()
"""

GOTO_FIRST_PAGE_JS = r"""
(() => {
  const active = document.querySelector('.core-pagination-item-active, .arco-pagination-item-active');
  const current = active ? String(active.innerText || '').trim() : '';
  if (!current || current === '1') {
    return JSON.stringify({ok: true, already: true, page: current || '1'});
  }
  const item = [...document.querySelectorAll('.core-pagination-item, .arco-pagination-item')].find(el =>
    (el.innerText || '').trim() === '1'
  );
  if (!item) return JSON.stringify({ok: false, reason: 'no-page-1', page: current});
  setTimeout(() => item.click(), 0);
  return JSON.stringify({ok: true, clicked: true, deferred: true, page: current});
})()
"""

RESET_COMPLETED_FILTER_JS = r"""
(() => {
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  const button = [...document.querySelectorAll('button, span, a')].find(el => {
    const text = (el.innerText || '').trim();
    return text === '重置' || text === 'Reset';
  });
  if (!button) {
    return JSON.stringify({ok: false, reason: 'no-reset'});
  }
  button.click();
  return JSON.stringify({ok: true, clicked: true});
})()
"""

WAIT_FOR_CREATOR_ROW_JS_TMPL = r"""
((name) => {
  const want = String(name || '').trim().toLowerCase();
  const rows = [...document.querySelectorAll('table tbody tr')];
  const dataRows = rows.filter(tr => {
    const text = String(tr.innerText || '');
    return /查看内容/.test(text) && !/列表为空|暂无数据|no data/i.test(text);
  });
  const matched = dataRows.filter(tr => String(tr.innerText || '').toLowerCase().includes(want));
  const next = document.querySelector(
    '.core-pagination-item-next:not(.core-pagination-item-disabled), ' +
    '.arco-pagination-item-next:not(.arco-pagination-item-disabled), ' +
    'li[aria-label="下一页"]:not([aria-disabled="true"]), ' +
    'button[aria-label="下一页"]:not([disabled])'
  );
  const nextDisabled = !next ||
    (next.classList && (
      next.classList.contains('core-pagination-item-disabled') ||
      next.classList.contains('arco-pagination-item-disabled')
    )) ||
    next.getAttribute('aria-disabled') === 'true' ||
    next.hasAttribute('disabled');
  return JSON.stringify({
    ok: true,
    row_count: rows.length,
    data_row_count: dataRows.length,
    matched_count: matched.length,
    can_next: !nextDisabled,
    empty: /列表为空|暂无数据|no data/i.test(String((rows[0] && rows[0].innerText) || '')),
  });
})(%NAME%)
"""


def reset_completed_filters(store_id: str, *, wait: float = 1.5) -> dict[str, Any]:
    result = zclaw_exec(store_id, RESET_COMPLETED_FILTER_JS)
    if isinstance(result, dict) and result.get("ok") and result.get("clicked"):
        time.sleep(max(1.0, wait))
    visible = list_visible_completed_creators(store_id)
    current_page = str((visible or {}).get("page") or "").strip()
    if current_page in {"", "1"}:
        first = {"ok": True, "already": True, "page": current_page or "1"}
    else:
        first = zclaw_exec(store_id, GOTO_FIRST_PAGE_JS, timeout=20)
        if isinstance(first, dict) and first.get("clicked"):
            time.sleep(max(0.8, wait))
    if isinstance(result, dict):
        result["first_page"] = first
        return result
    return {"ok": False, "reason": str(result), "first_page": first}


def list_visible_completed_creators(store_id: str) -> dict[str, Any]:
    result = zclaw_exec(store_id, LIST_VISIBLE_CREATORS_JS)
    return result if isinstance(result, dict) else {"ok": False, "reason": str(result)}


def scan_completed_table_creators(
    store_id: str,
    *,
    max_pages: int = 80,
    page_wait: float = 0.8,
) -> dict[str, Any]:
    """One unfiltered pass over the completed table. Search box is not used."""
    ensure_completed_tab(store_id)
    reset = reset_completed_filters(store_id, wait=page_wait)
    found: dict[str, dict[str, Any]] = {}
    pages_scanned = 0
    for page_number in range(max(1, int(max_pages))):
        pages_scanned = page_number + 1
        visible = list_visible_completed_creators(store_id)
        for item in visible.get("names") or []:
            if not isinstance(item, dict):
                continue
            for key in (item.get("name"), item.get("nick")):
                handle = str(key or "").strip().lower()
                if handle and handle not in found:
                    found[handle] = {
                        "name": str(item.get("name") or ""),
                        "nick": str(item.get("nick") or ""),
                        "page": pages_scanned,
                    }
        if not visible.get("can_next"):
            break
        clicked = click_next(store_id, page_wait=page_wait)
        if not clicked.get("ok"):
            break
    return {
        "ok": True,
        "pages": pages_scanned,
        "creators": found,
        "reset": reset,
    }


def _wait_for_creator_row(store_id: str, creator_handle: str, *, wait: float = 4.0) -> dict[str, Any]:
    name = str(creator_handle or "").strip()
    deadline = time.monotonic() + max(0.8, wait)
    last: dict[str, Any] = {}
    script = WAIT_FOR_CREATOR_ROW_JS_TMPL.replace("%NAME%", _js_string(name))
    while time.monotonic() < deadline:
        last = zclaw_exec(store_id, script)
        if not isinstance(last, dict):
            time.sleep(0.3)
            continue
        if int(last.get("matched_count") or 0) > 0:
            return last
        if int(last.get("data_row_count") or 0) > 0:
            return last
        time.sleep(0.3)
    return last if isinstance(last, dict) else {"ok": False, "reason": str(last)}


def locate_completed_creator_row(
    store_id: str,
    creator_handle: str,
    *,
    max_pages: int = 80,
    page_wait: float = 1.2,
) -> dict[str, Any]:
    """Reset filters, then page through the unfiltered completed table."""
    name = str(creator_handle or "").strip()
    if not name:
        return {"ok": False, "reason": "empty-creator", "row_visible": False}
    reset = reset_completed_filters(store_id)
    ensure_completed_tab(store_id)
    pages_scanned = 0
    last_wait: dict[str, Any] = {}
    for page_number in range(max(1, int(max_pages))):
        pages_scanned = page_number + 1
        last_wait = _wait_for_creator_row(store_id, name, wait=max(1.5, page_wait))
        if int(last_wait.get("matched_count") or 0) > 0:
            return {
                "ok": True,
                "row_visible": True,
                "page": pages_scanned,
                "reset": reset,
                "row_wait": last_wait,
            }
        if not last_wait.get("can_next"):
            break
        clicked = click_next(store_id, page_wait=page_wait)
        if not clicked.get("ok"):
            break
    return {
        "ok": False,
        "reason": "row-not-found",
        "row_visible": False,
        "page": pages_scanned,
        "reset": reset,
        "row_wait": last_wait,
    }


def search_completed_creator(store_id: str, creator_handle: str, *, wait: float = 1.5) -> dict[str, Any]:
    name = str(creator_handle or "").strip()
    if not name:
        return {"ok": False, "reason": "empty-creator"}
    selected = zclaw_exec(store_id, SELECT_CREATOR_NICKNAME_JS)
    if not isinstance(selected, dict) or not selected.get("ok"):
        return selected if isinstance(selected, dict) else {"ok": False, "reason": str(selected)}
    if selected.get("clicked"):
        time.sleep(0.4)
    script = SEARCH_CREATOR_JS_TMPL.replace("%NAME%", _js_string(name))
    result = zclaw_exec(store_id, script)
    if not isinstance(result, dict) or not result.get("ok"):
        return result if isinstance(result, dict) else {"ok": False, "reason": str(result)}
    waited = _wait_for_creator_row(store_id, name, wait=max(3.0, wait + 2.0))
    result["row_wait"] = waited
    result["row_visible"] = int(waited.get("matched_count") or 0) > 0
    return result


def inspect_wanted_completed_creators(
    store_id: str,
    wanted_handles: list[str],
    *,
    max_pages: int = 80,
    page_wait: float = 0.8,
    inspect_wait: float = 1.5,
) -> dict[str, dict[str, Any]]:
    """Reset once, page the unfiltered table, and inspect drawers as names appear."""
    wanted = {
        str(handle or "").strip().lower()
        for handle in wanted_handles
        if str(handle or "").strip()
    }
    if not wanted:
        return {}
    ensure_completed_tab(store_id)
    reset_completed_filters(store_id, wait=page_wait)
    inspected: dict[str, dict[str, Any]] = {}
    for _page_number in range(max(1, int(max_pages))):
        visible = list_visible_completed_creators(store_id)
        for item in visible.get("names") or []:
            if not isinstance(item, dict):
                continue
            aliases = {
                str(item.get("name") or "").strip().lower(),
                str(item.get("nick") or "").strip().lower(),
            }
            aliases.discard("")
            targets = aliases & wanted
            if not targets or any(name in inspected for name in targets):
                continue
            display_name = str(item.get("name") or item.get("nick") or "").strip()
            result = read_view_content_on_current_page(
                store_id,
                display_name,
                wait=inspect_wait,
            )
            for name in targets:
                inspected[name] = result
            if len(inspected) >= len(wanted):
                return inspected
        if not visible.get("can_next"):
            break
        clicked = click_next(store_id, page_wait=page_wait)
        if not clicked.get("ok"):
            break
    return inspected


def inspect_view_content(
    store_id: str,
    creator_handle: str,
    *,
    wait: float = 1.5,
) -> dict[str, Any]:
    """Locate one creator in the unfiltered table and read 「查看内容」."""
    handle = str(creator_handle or "").strip()
    if not handle:
        return {"ok": False, "reason": "empty-creator", "panel_text": "", "links": []}
    found = inspect_wanted_completed_creators(
        store_id,
        [handle],
        inspect_wait=wait,
    )
    return found.get(handle.lower()) or {
        "ok": False,
        "reason": "row-not-found",
        "panel_text": "",
        "links": [],
    }


def read_view_content_on_current_page(
    store_id: str,
    creator_handle: str,
    *,
    wait: float = 1.5,
) -> dict[str, Any]:
    """Click 「查看内容」 on the current page only. Never sends."""
    name = str(creator_handle or "").strip()
    open_script = OPEN_VIEW_CONTENT_JS_TMPL.replace("%NAME%", _js_string(name))
    opened = zclaw_exec(store_id, open_script)
    if not isinstance(opened, dict) or not opened.get("ok"):
        return {
            "ok": False,
            "reason": (opened or {}).get("reason") if isinstance(opened, dict) else "open-failed",
            "panel_text": "",
            "links": [],
        }
    time.sleep(max(1.5, wait))
    inspected = zclaw_exec(store_id, INSPECT_CONTENT_PANEL_JS)
    if not isinstance(inspected, dict) or not inspected.get("ok"):
        return {
            "ok": False,
            "reason": "panel-unreadable",
            "panel_text": "",
            "links": [],
            "opened": opened,
        }
    result = {
        "ok": True,
        "reason": "",
        "panel_text": str(inspected.get("panel_text") or ""),
        "links": list(inspected.get("links") or []),
        "has_panel": bool(inspected.get("has_panel")),
        "panel_kind": str(inspected.get("panel_kind") or ""),
        "video_count": inspected.get("video_count"),
        "live_count": inspected.get("live_count"),
        "opened": opened,
    }
    zclaw_exec(
        store_id,
        """(() => {
          const drawer = document.querySelector('.core-drawer, .pulse-drawer, .arco-drawer');
          const confirm = drawer && [...drawer.querySelectorAll('button, span')].find(el => {
            const text = (el.innerText || '').trim();
            return text === '确定' || text === 'OK';
          });
          if (confirm) confirm.click();
          else document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
          return JSON.stringify({ok: true});
        })()""",
    )
    return result
