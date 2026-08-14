#!/usr/bin/env python3
"""联盟「达人消息」：打开会话、读最近消息、可选发送。

默认只读。真正点「发送」仅当调用方传入 execute=True。
禁止点「邀请」。
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote

from .zclaw import visit_page, zclaw_exec, zclaw_invoke

IM_URL = (
    "https://affiliate.tiktokshopglobalselling.com/seller/im"
    "?shop_region=US&enter_from=nav_im_entry"
)

CLICK_MESSAGE_JS = r"""
(() => {
  window.__opened = window.__opened || [];
  if (!window.__openHooked) {
    const orig = window.open;
    window.open = function() {
      try { window.__opened.push(Array.from(arguments).map(String).slice(0, 3)); } catch (e) {}
      return orig.apply(this, arguments);
    };
    window.__openHooked = true;
  }
  const btn = [...document.querySelectorAll('#creator-detail-profile-container button')]
    .find(el => el.querySelector('.alliance-icon-Message') && !el.disabled
      && !el.classList.contains('core-btn-disabled'));
  if (!btn) {
    return JSON.stringify({ok: false, reason: 'no-message-btn', href: location.href});
  }
  btn.click();
  return JSON.stringify({
    ok: true,
    href: location.href,
    opened: window.__opened || []
  });
})()
"""

INSPECT_IM_JS = r"""
(() => {
  const text = ((document.body && document.body.innerText) || '').replace(/\u00a0/g, ' ');
  const input = document.querySelector('textarea, [placeholder*="发送消息"], [placeholder*="Send a message"]');
  const sendBtns = [...document.querySelectorAll('button,span,div')].filter(el => {
    const t = (el.innerText || '').trim();
    return t === '发送' || t === 'Send';
  }).slice(0, 6).map(el => ({
    tag: el.tagName,
    disabled: !!(el.disabled || /disabled/.test(String(el.className || ''))),
    cls: String(el.className || '').slice(0, 80)
  }));
  return JSON.stringify({
    href: location.href,
    title: document.title || '',
    onIm: /\/seller\/im/.test(location.href),
    hasComposer: !!(input || /发送消息|0\/2000/.test(text)),
    sendBtns,
    text: text.slice(0, 4000)
  });
})()
"""

SEARCH_USER_JS_TMPL = r"""
((name) => {
  const input = document.querySelector('input[placeholder*="搜索"], input.core-input');
  if (!input) return JSON.stringify({ok: false, reason: 'no-search'});
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  input.focus();
  const tracker = input._valueTracker;
  if (tracker) tracker.setValue('');
  setter.call(input, name);
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  return JSON.stringify({ok: true, value: input.value});
})(%NAME%)
"""

CLICK_CONV_JS_TMPL = r"""
((name) => {
  const want = String(name || '').trim().toLowerCase();
  if (!want) return JSON.stringify({ok: false, reason: 'empty-name'});
  const cards = [...document.querySelectorAll('div')].filter(el => {
    const cls = String(el.className || '');
    return /contactCard/.test(cls);
  });
  const pool = cards.length ? cards : [...document.querySelectorAll('.arco-list-item')];
  for (const card of pool) {
    const t = (card.innerText || '').trim();
    if (!t || t.length > 400) continue;
    const head = t.split('\n')[0] || '';
    const blob = t.toLowerCase();
    if (blob.includes(want) || head.toLowerCase() === want) {
      const fiberKey = Object.keys(card).find(key =>
        key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
      );
      const fiber = fiberKey ? card[fiberKey] : null;
      const props = fiber && fiber.memoizedProps;
      const parentProps = fiber && fiber.return && fiber.return.memoizedProps;
      const contact = parentProps && parentProps.contact;
      if (props && typeof props.onClick === 'function') {
        props.onClick({
          currentTarget: card,
          target: card,
          preventDefault() {},
          stopPropagation() {},
        });
        return JSON.stringify({
          ok: true,
          via: 'react-onClick',
          preview: t.slice(0, 80),
          conversation_id: contact && contact.conversationId
            ? String(contact.conversationId)
            : '',
          creator_id: contact && contact.userInfo && contact.userInfo.userId
            ? String(contact.userInfo.userId)
            : '',
        });
      }
      card.click();
      return JSON.stringify({ok: true, via: 'card', preview: t.slice(0, 80)});
    }
  }
  return JSON.stringify({ok: false, reason: 'conv-not-found', n: pool.length});
})(%NAME%)
"""

FILL_MESSAGE_JS_TMPL = r"""
((body, doSend) => {
  const box = document.querySelector('textarea')
    || document.querySelector('[placeholder*="发送消息"]')
    || document.querySelector('[placeholder*="Send a message"]')
    || document.querySelector('[contenteditable="true"]');
  if (!box) return JSON.stringify({ok: false, reason: 'no-composer'});
  if (box.tagName === 'TEXTAREA' || box.tagName === 'INPUT') {
    const setter = Object.getOwnPropertyDescriptor(
      box.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype,
      'value'
    ).set;
    box.focus();
    const tracker = box._valueTracker;
    if (tracker) tracker.setValue('');
    setter.call(box, body);
    box.dispatchEvent(new Event('input', {bubbles: true}));
    box.dispatchEvent(new Event('change', {bubbles: true}));
  } else {
    box.focus();
    box.innerText = body;
    box.dispatchEvent(new Event('input', {bubbles: true}));
  }
  if (!doSend) {
    return JSON.stringify({ok: true, filled: true, sent: false, href: location.href});
  }
  const send = [...document.querySelectorAll('button,span,div')].find(el => {
    const t = (el.innerText || '').trim();
    if (t !== '发送' && t !== 'Send') return false;
    if (el.disabled || /disabled/.test(String(el.className || ''))) return false;
    const r = el.getBoundingClientRect();
    return r.width > 20 && r.height > 12;
  });
  if (!send) return JSON.stringify({ok: false, reason: 'no-send-btn', filled: true});
  send.click();
  return JSON.stringify({ok: true, filled: true, sent: true, href: location.href});
})(%BODY%, %DO_SEND%)
"""


def _js_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def inspect_im(store_id: str) -> dict[str, Any]:
    ret = zclaw_exec(store_id, INSPECT_IM_JS)
    return ret if isinstance(ret, dict) else {"ok": False, "raw": ret}


def open_im_from_detail(store_id: str, *, wait: float = 3.5) -> dict[str, Any]:
    clicked = zclaw_exec(store_id, CLICK_MESSAGE_JS)
    if not isinstance(clicked, dict):
        return {"ok": False, "error": f"click-bad:{clicked!r}"[:200]}
    time.sleep(wait)
    opened = clicked.get("opened") or []
    target = ""
    for item in opened:
        if isinstance(item, list) and item:
            url = str(item[0] or "")
            if "/seller/im" in url:
                target = url
                break
        elif isinstance(item, str) and "/seller/im" in item:
            target = item
            break
    probe = inspect_im(store_id)
    if probe.get("onIm"):
        return {"ok": True, "via": "navigated", "im": probe}
    if target:
        visit_page(store_id, target)
        time.sleep(wait)
        probe = inspect_im(store_id)
        if probe.get("onIm"):
            return {"ok": True, "via": "window.open", "im": probe, "url": target}
    return {"ok": False, "error": "im-not-opened", "click": clicked, "im": probe}


def open_im_inbox(store_id: str, *, shop_id: str = "", wait: float = 3.0) -> dict[str, Any]:
    url = IM_URL
    if shop_id:
        url += f"&shop_id={quote(shop_id)}"
    visit_page(store_id, url)
    time.sleep(wait)
    probe = inspect_im(store_id)
    if not probe.get("onIm"):
        return {"ok": False, "error": "not-on-im", "im": probe}
    return {"ok": True, "via": "visit", "im": probe}


def search_and_open_conversation(
    store_id: str,
    creator_name: str,
    *,
    wait: float = 2.0,
) -> dict[str, Any]:
    name = (creator_name or "").strip()
    if not name:
        return {"ok": False, "error": "empty-creator"}
    searched: dict[str, Any] = {}
    clicked: dict[str, Any] = {"ok": False, "reason": "conv-not-found"}
    for attempt in range(3):
        current_search = zclaw_exec(
            store_id,
            SEARCH_USER_JS_TMPL.replace("%NAME%", _js_str(name)),
        )
        if isinstance(current_search, dict):
            searched = current_search
        time.sleep(max(1.0, wait))
        current_click = zclaw_exec(
            store_id,
            CLICK_CONV_JS_TMPL.replace("%NAME%", _js_str(name)),
        )
        if isinstance(current_click, dict):
            clicked = current_click
        if isinstance(clicked, dict) and clicked.get("ok"):
            break
        if attempt == 2:
            try:
                el = zclaw_invoke(
                    "click_element",
                    {"storeId": store_id, "selector": ".arco-list-item", "hint": name},
                    timeout=60,
                )
                if el.get("ok"):
                    clicked = {"ok": True, "via": "click_element", "raw": el}
            except Exception as error:
                clicked = {"ok": False, "reason": str(error)}
    time.sleep(max(1.5, wait))
    if isinstance(clicked, dict) and clicked.get("ok"):
        return {"ok": True, "search": searched, "click": clicked}
    return {
        "ok": False,
        "error": (clicked or {}).get("reason") if isinstance(clicked, dict) else "click-failed",
        "search": searched,
        "click": clicked,
    }


def fill_or_send_message(
    store_id: str,
    body: str,
    *,
    execute: bool = False,
) -> dict[str, Any]:
    script = FILL_MESSAGE_JS_TMPL.replace("%BODY%", _js_str(body)).replace(
        "%DO_SEND%", "true" if execute else "false"
    )
    ret = zclaw_exec(store_id, script)
    if not isinstance(ret, dict):
        return {"ok": False, "error": f"fill-bad:{ret!r}"[:200]}
    if not ret.get("ok"):
        return {"ok": False, "error": ret.get("reason") or str(ret), "detail": ret}
    return ret


def conversation_already_has(store_id: str, predicate) -> bool:
    probe = inspect_im(store_id)
    text = str(probe.get("text") or "")
    return bool(predicate(text))
