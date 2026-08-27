#!/usr/bin/env python3
"""样品申请「已完成」列表：切 tab、搜达人、只读打开「查看内容」。

不点同意 / 拒绝 / 发货 / 发送。搜索框填值复用 IM 搜索同一套 React setter。
"""
from __future__ import annotations

import json
import time
from typing import Any

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

SEARCH_CREATOR_JS_TMPL = r"""
((name) => {
  const input = document.querySelector(
    'input[placeholder*="搜索"], input[placeholder*="达人"], input.core-input, input.arco-input'
  );
  if (!input) return JSON.stringify({ok: false, reason: 'no-search'});
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  input.focus();
  const tracker = input._valueTracker;
  if (tracker) tracker.setValue('');
  setter.call(input, name);
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
  input.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', bubbles: true}));
  return JSON.stringify({ok: true, value: input.value});
})(%NAME%)
"""

OPEN_VIEW_CONTENT_JS_TMPL = r"""
((creatorName) => {
  const want = String(creatorName || '').trim().toLowerCase();
  const rows = [...document.querySelectorAll('table tbody tr')];
  let matched = null;
  for (const tr of rows) {
    const rowText = String(tr.innerText || '').toLowerCase();
    if (want && rowText.includes(want)) {
      matched = tr;
      break;
    }
  }
  if (!matched && rows.length === 1) {
    matched = rows[0];
  }
  if (!matched) {
    return JSON.stringify({ok: false, reason: 'row-not-found', href: location.href});
  }
  const button = [...matched.querySelectorAll('button, a, span, div')].find(el =>
    /查看内容|View content|View Content/i.test((el.innerText || '').trim())
    && String((el.innerText || '').trim()).length < 20
  );
  if (!button) {
    return JSON.stringify({
      ok: false,
      reason: 'no-view-content-button',
      href: location.href,
      row_text: String(matched.innerText || '').slice(0, 200),
    });
  }
  button.click();
  return JSON.stringify({
    ok: true,
    clicked: true,
    href: location.href,
    button_text: String(button.innerText || '').trim().slice(0, 40),
  });
})(%NAME%)
"""

INSPECT_CONTENT_PANEL_JS = r"""
(() => {
  const dialog = document.querySelector(
    '.core-modal, .arco-modal, [role="dialog"], [class*="drawer"], [class*="Drawer"]'
  );
  const root = dialog || document.body;
  const text = String((root && root.innerText) || '').replace(/\u00a0/g, ' ').trim();
  const links = [...(root ? root.querySelectorAll('a[href]') : [])]
    .map(el => String(el.href || '').trim())
    .filter(href => href);
  return JSON.stringify({
    ok: true,
    href: location.href,
    has_panel: !!dialog,
    panel_text: text.slice(0, 4000),
    links: links.slice(0, 20),
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


def search_completed_creator(store_id: str, creator_handle: str, *, wait: float = 1.5) -> dict[str, Any]:
    name = str(creator_handle or "").strip()
    if not name:
        return {"ok": False, "reason": "empty-creator"}
    script = SEARCH_CREATOR_JS_TMPL.replace("%NAME%", _js_string(name))
    result = zclaw_exec(store_id, script)
    if isinstance(result, dict) and result.get("ok"):
        time.sleep(max(0.5, wait))
    return result if isinstance(result, dict) else {"ok": False, "reason": str(result)}


def inspect_view_content(
    store_id: str,
    creator_handle: str,
    *,
    wait: float = 1.5,
) -> dict[str, Any]:
    """Click 「查看内容」 once and read the panel. Never sends."""
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
    time.sleep(max(0.5, wait))
    inspected = zclaw_exec(store_id, INSPECT_CONTENT_PANEL_JS)
    if not isinstance(inspected, dict) or not inspected.get("ok"):
        return {
            "ok": False,
            "reason": "panel-unreadable",
            "panel_text": "",
            "links": [],
            "opened": opened,
        }
    return {
        "ok": True,
        "reason": "",
        "panel_text": str(inspected.get("panel_text") or ""),
        "links": list(inspected.get("links") or []),
        "has_panel": bool(inspected.get("has_panel")),
        "opened": opened,
    }
