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
  const existing = document.querySelector('textarea, [placeholder*="发送消息"], [placeholder*="Send a message"]');
  const detailOpen = !!document.querySelector('#creator-detail-profile-container') || /\/seller\/im/.test(location.href);
  if (existing && detailOpen) {
    return JSON.stringify({
      ok: true,
      already: true,
      href: location.href,
      opened: window.__opened || []
    });
  }
  const btn = [...document.querySelectorAll('#creator-detail-profile-container button')]
    .find(el => el.querySelector('.alliance-icon-Message') && !el.disabled
      && !el.classList.contains('core-btn-disabled'));
  if (!btn) {
    return JSON.stringify({ok: false, reason: 'no-message-btn', href: location.href});
  }
  const fiberKey = Object.keys(btn).find(key =>
    key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
  );
  let fiber = fiberKey ? btn[fiberKey] : null;
  for (let depth = 0; depth < 16 && fiber; depth++, fiber = fiber.return) {
    const props = fiber.memoizedProps || {};
    if (typeof props.handleClick === 'function') {
      props.handleClick();
      return JSON.stringify({
        ok: true,
        via: 'handleClick',
        depth,
        creatorId: props.creatorId ? String(props.creatorId) : '',
        href: location.href,
        opened: window.__opened || []
      });
    }
  }
  fiber = fiberKey ? btn[fiberKey] : null;
  if (fiber && fiber.memoizedProps && typeof fiber.memoizedProps.onClick === 'function') {
    fiber.memoizedProps.onClick({
      currentTarget: btn,
      target: btn,
      preventDefault() {},
      stopPropagation() {},
    });
    return JSON.stringify({
      ok: true,
      via: 'react-onClick',
      href: location.href,
      opened: window.__opened || []
    });
  }
  btn.click();
  return JSON.stringify({
    ok: true,
    via: 'native',
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
  const cards = [...document.querySelectorAll('div')].filter(el => {
    return /contactCard/.test(String(el.className || ''));
  });
  const selected = cards.find(el => /selected/.test(String(el.className || ''))) || null;
  let selectedUserId = '';
  let selectedConversationId = '';
  if (selected) {
    const fiberKey = Object.keys(selected).find(key =>
      key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
    );
    let fiber = fiberKey ? selected[fiberKey] : null;
    for (let depth = 0; depth < 8 && fiber; depth++, fiber = fiber.return) {
      const contact = fiber.memoizedProps && fiber.memoizedProps.contact;
      if (!contact) continue;
      selectedConversationId = contact.conversationId ? String(contact.conversationId) : '';
      selectedUserId = contact.userInfo && contact.userInfo.userId
        ? String(contact.userInfo.userId) : '';
      break;
    }
  }
  let threadText = '';
  if (input) {
    let node = input.parentElement;
    for (let depth = 0; depth < 14 && node; depth++, node = node.parentElement) {
      const cardCount = [...node.querySelectorAll('div')].filter(el =>
        /contactCard/.test(String(el.className || ''))
      ).length;
      if (cardCount > 0) break;
      const chunk = String(node.innerText || '').replace(/\u00a0/g, ' ').trim();
      if (chunk) threadText = chunk;
    }
  }
  const search = document.querySelector('input[placeholder*="搜索"], input.core-input');
  const dock = document.querySelector('[class*="entryWrapper"]');
  const detailProfile = document.querySelector('#creator-detail-profile-container');
  return JSON.stringify({
    href: location.href,
    title: document.title || '',
    onIm: /\/seller\/im/.test(location.href),
    hasComposer: !!(input || /发送消息|0\/2000/.test(text)),
    hasDock: !!(dock || /聊天数/.test(text)),
    hasSearch: !!search,
    hasDetailProfile: !!detailProfile,
    selected_preview: selected ? String(selected.innerText || '').slice(0, 200) : '',
    selected_user_id: selectedUserId,
    selected_conversation_id: selectedConversationId,
    sendBtns,
    thread_text: threadText.slice(0, 4000),
    text: text.slice(0, 4000)
  });
})()
"""

SEARCH_USER_JS_TMPL = r"""
((name) => {
  // 只在 /seller/im 页面搜索 IM 会话；不碰页面其它搜索（如样品页「搜索达人昵称」）。
  const input = [...document.querySelectorAll('input')].find(el =>
    /搜索|search/i.test(String(el.placeholder || ''))
  );
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

EXPAND_CHAT_DOCK_JS = r"""
(() => {
  if (document.querySelector('textarea, [placeholder*="发送消息"], [placeholder*="Send a message"]')) {
    return JSON.stringify({ok: true, already: true});
  }
  const entry = document.querySelector('[class*="entryWrapper"]')
    || [...document.querySelectorAll('div')].find(el => {
         const t = (el.innerText || '').trim();
         return t === '聊天数' || /^聊天数\n\d+/.test(t);
       });
  if (!entry) {
    return JSON.stringify({ok: false, reason: 'no-chat-dock'});
  }
  const fiberKey = Object.keys(entry).find(key =>
    key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
  );
  let fiber = fiberKey ? entry[fiberKey] : null;
  for (let depth = 0; depth < 10 && fiber; depth++, fiber = fiber.return) {
    const props = fiber.memoizedProps;
    if (props && typeof props.onClick === 'function') {
      props.onClick({
        currentTarget: entry,
        target: entry,
        preventDefault() {},
        stopPropagation() {},
      });
      return JSON.stringify({ok: true, via: 'react-onClick', depth});
    }
  }
  entry.click();
  return JSON.stringify({ok: true, via: 'native'});
})()
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

# 样品申请等业务页右下角「聊天数」浮动入口 → 弹出聊天数面板。
OPEN_CHAT_PANEL_JS = r"""
(() => {
  if ([...document.querySelectorAll('.core-modal-content')].some(m => /最近联系人/.test(m.innerText || ''))) {
    return JSON.stringify({ok: true, already: true});
  }
  const entry = [...document.querySelectorAll('div')].find(el => {
    const cls = String(el.className || '');
    return /entryWrapper/.test(cls) && el.getBoundingClientRect().width > 0;
  }) || [...document.querySelectorAll('div,button,span')].find(el => {
    const t = (el.innerText || '').trim();
    return t === '聊天数' && el.getBoundingClientRect().width > 0;
  });
  if (!entry) return JSON.stringify({ok: false, reason: 'no-chat-dock'});
  const fiberKey = Object.keys(entry).find(k =>
    k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
  );
  let fiber = fiberKey ? entry[fiberKey] : null;
  let via = 'react-onClick';
  let clicked = false;
  for (let depth = 0; depth < 10 && fiber; depth++, fiber = fiber.return) {
    const props = fiber.memoizedProps;
    if (props && typeof props.onClick === 'function') {
      props.onClick({
        currentTarget: entry,
        target: entry,
        preventDefault() {},
        stopPropagation() {},
      });
      clicked = true;
      break;
    }
  }
  if (!clicked) {
    entry.click();
    via = 'native';
  }
  return JSON.stringify({ok: true, via});
})()
"""

# 聊天数面板标题「聊天数」旁的编辑图标（新消息入口）。
CLICK_NEW_MESSAGE_BTN_JS = r"""
(() => {
  const modal = [...document.querySelectorAll('.core-modal-content')].find(m =>
    /最近联系人/.test(m.innerText || '')
  );
  if (!modal) return JSON.stringify({ok: false, reason: 'no-chat-panel'});
  const btn = [...modal.querySelectorAll('button')].find(b => {
    const svg = b.querySelector('svg.arco-icon-edit');
    return !!svg && b.getBoundingClientRect().width > 0;
  });
  if (!btn) return JSON.stringify({ok: false, reason: 'no-edit-btn'});
  const fiberKey = Object.keys(btn).find(k =>
    k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
  );
  let fiber = fiberKey ? btn[fiberKey] : null;
  for (let depth = 0; depth < 10 && fiber; depth++, fiber = fiber.return) {
    const props = fiber.memoizedProps;
    if (props && typeof props.onClick === 'function') {
      props.onClick({
        currentTarget: btn,
        target: btn,
        preventDefault() {},
        stopPropagation() {},
      });
      return JSON.stringify({ok: true, via: 'react-onClick', depth});
    }
  }
  btn.click();
  return JSON.stringify({ok: true, via: 'native'});
})()
"""

# 「新消息」抽屉「发送给」输入框：填达人 ID/昵称并回车。
FILL_NEW_MESSAGE_SEARCH_JS_TMPL = r"""
((value) => {
  const drawer = [...document.querySelectorAll('.core-drawer-content')].find(m =>
    /新消息|发送给/.test(m.innerText || '')
  );
  if (!drawer) return JSON.stringify({ok: false, reason: 'no-new-message-drawer'});
  const input = drawer.querySelector('input.core-input');
  if (!input) return JSON.stringify({ok: false, reason: 'no-send-to-input'});
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  input.focus();
  const tracker = input._valueTracker;
  if (tracker) tracker.setValue('');
  setter.call(input, value);
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  input.dispatchEvent(new KeyboardEvent('keydown', {
    key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true,
  }));
  input.dispatchEvent(new KeyboardEvent('keyup', {
    key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true,
  }));
  return JSON.stringify({ok: true, value: input.value});
})(%VALUE%)
"""

# 「新消息」抽屉里的搜索结果行：行内 button 挂 React onClick，点击打开会话。
CLICK_NEW_MESSAGE_RESULT_JS_TMPL = r"""
((want) => {
  const query = String(want || '').trim().toLowerCase();
  if (!query) return JSON.stringify({ok: false, reason: 'empty-query'});
  const drawer = [...document.querySelectorAll('.core-drawer-content')].find(m =>
    /新消息|发送给/.test(m.innerText || '')
  );
  if (!drawer) return JSON.stringify({ok: false, reason: 'no-new-message-drawer'});
  const row = [...drawer.querySelectorAll('div')].find(el => {
    const cls = String(el.className || '');
    return /hover:bg-state-hover/.test(cls)
      && (el.innerText || '').toLowerCase().includes(query)
      && el.getBoundingClientRect().width > 0;
  });
  if (!row) return JSON.stringify({ok: false, reason: 'no-result-row'});
  const btn = row.querySelector('button') || row;
  const fiberKey = Object.keys(btn).find(k =>
    k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
  );
  let fiber = fiberKey ? btn[fiberKey] : null;
  for (let depth = 0; depth < 10 && fiber; depth++, fiber = fiber.return) {
    const props = fiber.memoizedProps;
    if (props && typeof props.onClick === 'function') {
      props.onClick({
        currentTarget: btn,
        target: btn,
        preventDefault() {},
        stopPropagation() {},
      });
      return JSON.stringify({ok: true, via: 'react-onClick', depth});
    }
  }
  btn.click();
  return JSON.stringify({ok: true, via: 'native'});
})(%WANT%)
"""


def _js_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def inspect_im(store_id: str) -> dict[str, Any]:
    ret = zclaw_exec(store_id, INSPECT_IM_JS)
    return ret if isinstance(ret, dict) else {"ok": False, "raw": ret}


def composer_ready(probe: dict[str, Any] | None) -> bool:
    return bool(isinstance(probe, dict) and probe.get("hasComposer"))


def conversation_matches(
    probe: dict[str, Any] | None,
    creator_name: str,
    creator_id: str = "",
) -> bool:
    """用弹层/列表里的选中会话核对达人，不用整页正文。

    详情页标题永远含当前达人名；整页 innerText 命中不能当成会话已打开。
    """
    if not isinstance(probe, dict):
        return False
    name = (creator_name or "").strip().lower()
    cid = str(creator_id or "").strip()
    selected_id = str(probe.get("selected_user_id") or "").strip()
    selected_preview = str(probe.get("selected_preview") or "").lower()
    if cid and selected_id and cid == selected_id:
        return True
    if name and name in selected_preview:
        return True
    return False


def im_thread_text(probe: dict[str, Any] | None) -> str:
    """当前会话气泡区正文。不要用整页 text：侧栏里别人的介绍会被误判成已发送。"""
    if not isinstance(probe, dict):
        return ""
    return str(probe.get("thread_text") or "").strip()


def thread_has_named_intro(probe: dict[str, Any] | None, creator_name: str) -> bool:
    from .message_templates import looks_like_intro

    thread = im_thread_text(probe)
    if not looks_like_intro(thread):
        return False
    name = (creator_name or "").strip().lower()
    if not name:
        return True
    return name in thread.lower()


def thread_looks_stale(probe: dict[str, Any] | None, creator_name: str) -> bool:
    """会话区有介绍话术，但问候的不是当前达人：多半是上一条会话残留。"""
    from .message_templates import looks_like_intro

    thread = im_thread_text(probe)
    name = (creator_name or "").strip().lower()
    return bool(name and looks_like_intro(thread) and name not in thread.lower())


def inspect_current_thread(
    store_id: str,
    creator_name: str,
    *,
    wait: float = 1.5,
) -> dict[str, Any]:
    probe = inspect_im(store_id)
    if thread_looks_stale(probe, creator_name):
        time.sleep(max(0.8, wait))
        probe = inspect_im(store_id)
    return probe


def _im_context_ready(probe: dict[str, Any] | None) -> bool:
    """浏览器里确实处于 IM 会话上下文（/seller/im 或达人详情弹层）。

    聊天数面板/旧会话残留也有 composer，但没有 IM 上下文；不能当成「已经打开」。
    """
    return bool(probe and (probe.get("onIm") or probe.get("hasDetailProfile")))


def _expand_chat_dock(store_id: str, *, wait: float) -> dict[str, Any]:
    expanded = zclaw_exec(store_id, EXPAND_CHAT_DOCK_JS)
    if not isinstance(expanded, dict):
        return {"ok": False, "error": f"expand-bad:{expanded!r}"[:200]}
    if not expanded.get("already"):
        time.sleep(max(1.0, wait))
    probe = inspect_im(store_id)
    if composer_ready(probe) and _im_context_ready(probe):
        return {"ok": True, "via": "overlay-expanded", "im": probe, "expand": expanded}
    return {"ok": False, "expand": expanded, "im": probe}


def open_im_from_detail(store_id: str, *, wait: float = 3.5) -> dict[str, Any]:
    """点详情页邀请旁的消息按钮，必要时再展开「聊天数」弹层。

    页面内弹层有输入框即成功，不要求跳到 /seller/im。永不点「邀请」。
    """
    probe = inspect_im(store_id)
    # 注意：聊天数面板/旧会话残留也会让 hasComposer 为真，但这不是本次点击的结果。
    # 只有页面真的在 /seller/im 或达人详情弹层（#creator-detail-profile-container）里，
    # 才算「已经打开」。
    if composer_ready(probe) and _im_context_ready(probe):
        via = "already-open-im" if probe.get("onIm") else "already-open-overlay"
        return {"ok": True, "via": via, "im": probe, "click": {"ok": True, "already": True}}
    clicked = zclaw_exec(store_id, CLICK_MESSAGE_JS)
    if not isinstance(clicked, dict):
        return {"ok": False, "error": f"click-bad:{clicked!r}"[:200]}
    if clicked.get("already"):
        probed_after = inspect_im(store_id)
        if composer_ready(probed_after) and _im_context_ready(probed_after):
            return {"ok": True, "via": "overlay", "im": probed_after, "click": clicked}
        return {"ok": False, "error": "already-but-unusable", "click": clicked, "im": probed_after}
    if not clicked.get("ok"):
        return {"ok": False, "error": "no-message-btn", "click": clicked, "im": probe}
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
    if composer_ready(probe) and _im_context_ready(probe):
        via = "navigated" if probe.get("onIm") else "overlay"
        return {"ok": True, "via": via, "im": probe, "click": clicked}
    expanded = _expand_chat_dock(store_id, wait=wait)
    if expanded.get("ok"):
        expanded["click"] = clicked
        return expanded
    if target:
        visit_page(store_id, target)
        time.sleep(wait)
        probe = inspect_im(store_id)
        if composer_ready(probe) and probe.get("onIm"):
            return {"ok": True, "via": "window.open", "im": probe, "url": target}
    return {
        "ok": False,
        "error": "im-not-opened",
        "click": clicked,
        "expand": expanded,
        "im": probe,
    }


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


def _try_open_via_new_message(
    store_id: str,
    creator_key: str,
    *,
    creator_name: str,
    wait: float,
) -> dict[str, Any]:
    name = str(creator_key or "").strip()
    if not name:
        return {"ok": False, "error": "empty-creator"}
    opened_panel = zclaw_exec(store_id, OPEN_CHAT_PANEL_JS)
    if not isinstance(opened_panel, dict) or not opened_panel.get("ok"):
        return {"ok": False, "error": "无法打开聊天数面板", "panel": opened_panel}
    if not opened_panel.get("already"):
        time.sleep(max(1.0, wait))
    opened_drawer = zclaw_exec(store_id, CLICK_NEW_MESSAGE_BTN_JS)
    if not isinstance(opened_drawer, dict) or not opened_drawer.get("ok"):
        return {"ok": False, "error": "无法打开新消息抽屉", "drawer": opened_drawer}
    time.sleep(max(1.0, wait))
    filled = zclaw_exec(
        store_id,
        FILL_NEW_MESSAGE_SEARCH_JS_TMPL.replace("%VALUE%", _js_str(name)),
    )
    if not isinstance(filled, dict) or not filled.get("ok"):
        return {"ok": False, "error": "发送给输入失败", "fill": filled}
    time.sleep(max(1.0, wait))
    clicked = zclaw_exec(
        store_id,
        CLICK_NEW_MESSAGE_RESULT_JS_TMPL.replace("%WANT%", _js_str(name)),
    )
    if not isinstance(clicked, dict) or not clicked.get("ok"):
        return {"ok": False, "error": "结果行未找到或点击失败", "click": clicked}
    time.sleep(max(1.5, wait))
    probe = inspect_im(store_id)
    # 会话右侧若有 composer，但选中的可能仍是上一会话；必须以选中卡/正文命中目标为准。
    needle = str(creator_name or name or "").strip().lower()
    selected_preview = str(probe.get("selected_preview") or "").lower()
    thread_text = str(probe.get("thread_text") or "").lower()
    verified = bool(needle and (needle in selected_preview or needle in thread_text))
    if not composer_ready(probe) or not verified:
        return {
            "ok": False,
            "error": "会话未确认打开" if composer_ready(probe) else "会话发送框未出现",
            "needle": needle,
            "selected_preview": selected_preview[:120],
            "click": clicked,
            "im": probe,
        }
    return {
        "ok": True,
        "via": "new-message",
        "panel": opened_panel,
        "drawer": opened_drawer,
        "fill": filled,
        "click": clicked,
        "im": probe,
    }


def open_conversation_via_new_message(
    store_id: str,
    creator_id: str = "",
    creator_name: str = "",
    *,
    wait: float = 2.0,
) -> dict[str, Any]:
    """业务页右下角「聊天数」→ 编辑图标「新消息」→ 「发送给」输入达人 ID。

    实测链路（2026-08-28）：entryWrapper 弹面板 → 面板标题旁
    ``arco-icon-edit`` 按钮开抽屉 → 抽屉 ``input.core-input`` 填达人
    ID/昵称回车 → 结果行「聊天」按钮（React ``onClick``）打开会话。
    不跳 /seller/im、不点「聊天数」里的最近联系人。

    先后用 creator_id、creator_name 两个关键词各尝试一次；点击后必须
    在选中卡/正文里确认命中目标达人，否则重试下一个关键词。
    """
    candidates = [key for key in (str(creator_id or "").strip(), str(creator_name or "").strip()) if key]
    name = str(creator_name or "").strip()
    if not candidates:
        return {"ok": False, "error": "empty-creator"}
    # IM 页（/seller/im）没有聊天数 dock；必须先回样品申请页再走该路径。
    probe = inspect_im(store_id)
    if probe.get("onIm"):
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(str(probe.get("href") or ""))
        query = parse_qs(parsed.query)
        shop_id = str((query.get("shop_id") or [""])[0]).strip()
        target = (
            "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request"
            "?shop_region=US&shop_id=" + quote(shop_id or "")
        )
        visit_page(store_id, target)
        # 跨域回样品页是 SPA 重载：complete 前 execute_script 会阻塞/失败。
        # 按实测（20-45s 必超时）等待页面稳定，超预算则放弃本次尝试。
        deadline_monotonic = time.monotonic() + 90.0
        ready = False
        while time.monotonic() < deadline_monotonic:
            time.sleep(3.0)
            try:
                settled = zclaw_exec(
                    store_id,
                    "(() => JSON.stringify({href: (location.href||''), ready: document.readyState, dock: !!document.querySelector('[class*=\"entryWrapper\"]')}))()",
                    timeout=25,
                )
                if (
                    isinstance(settled, dict)
                    and "sample-request" in str(settled.get("href") or "")
                    and settled.get("ready") == "complete"
                    and settled.get("dock")
                ):
                    ready = True
                    break
            except Exception:
                continue
        if not ready:
            return {"ok": False, "error": "样品申请页未就绪", "target": target[:120]}
    failures: list[dict[str, Any]] = []
    for candidate in candidates:
        attempt = _try_open_via_new_message(
            store_id,
            candidate,
            creator_name=name,
            wait=wait,
        )
        if attempt.get("ok"):
            return attempt
        failures.append({"key": candidate, "detail": attempt})
        # 面板/抽屉都打不开时，换关键词也无济于事；只有「搜不到/没点中」
        # 这类关键词相关失败才换下一个候选。
        if attempt.get("error") in {"无法打开聊天数面板", "无法打开新消息抽屉"}:
            break
    return {"ok": False, "error": "新消息路径未找到会话", "attempts": failures}


def search_and_open_conversation(
    store_id: str,
    creator_name: str,
    *,
    creator_id: str = "",
    wait: float = 2.0,
) -> dict[str, Any]:
    name = (creator_name or "").strip()
    if not name:
        return {"ok": False, "error": "empty-creator"}
    probe = inspect_im(store_id)
    if conversation_matches(probe, name, creator_id):
        return {
            "ok": True,
            "via": "already-selected",
            "search": {"ok": True, "skipped": True},
            "click": {
                "ok": True,
                "via": "already-selected",
                "conversation_id": probe.get("selected_conversation_id") or "",
                "creator_id": probe.get("selected_user_id") or "",
            },
        }
    searched: dict[str, Any] = {}
    clicked: dict[str, Any] = {"ok": False, "reason": "conv-not-found"}
    for attempt in range(3):
        current_search = zclaw_exec(
            store_id,
            SEARCH_USER_JS_TMPL.replace("%NAME%", _js_str(name)),
        )
        if isinstance(current_search, dict):
            searched = current_search
        # 详情弹层通常没有搜索框，仍可直接点 contactCard
        if searched.get("ok") or searched.get("reason") == "no-search":
            time.sleep(max(1.0, wait) if searched.get("ok") else 0.2)
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
    probe = inspect_im(store_id)
    if conversation_matches(probe, name, creator_id):
        return {
            "ok": True,
            "via": "selected-after-search",
            "search": searched,
            "click": clicked,
        }
    return {
        "ok": False,
        "error": (clicked or {}).get("reason") if isinstance(clicked, dict) else "click-failed",
        "search": searched,
        "click": clicked,
    }


def open_target_conversation(
    store_id: str,
    creator_name: str,
    *,
    creator_id: str = "",
    shop_id: str = "",
    wait: float = 3.5,
) -> dict[str, Any]:
    """优先详情页消息弹层，再走「聊天数 → 新消息」输入达人 ID，最后 /seller/im。永不点「邀请」。

    每级失败都继续降级：详情弹层搜索失败不直接抛错，而是落到「新消息」
    抽屉路径（实测在样品申请类业务页最稳）。
    """
    name = str(creator_name or "").strip()
    cid = str(creator_id or "").strip()
    from_detail = open_im_from_detail(store_id, wait=wait)
    if from_detail.get("ok"):
        clicked = search_and_open_conversation(
            store_id, name, creator_id=cid, wait=wait
        )
        if clicked.get("ok"):
            via = "detail-direct" if clicked.get("via") == "already-selected" else "detail+search"
            return {
                "ok": True,
                "via": via,
                "detail": from_detail,
                "click": clicked,
            }
        # 详情弹层打开成功但没搜到目标会话：继续降级，不直接失败。
        from_detail = {"ok": False, "error": "detail-search-failed", "detail": from_detail, "click": clicked}
    new_message = open_conversation_via_new_message(
        store_id, creator_id=cid, creator_name=name, wait=wait
    )
    if new_message.get("ok"):
        return {"ok": True, "via": "new-message", "new_message": new_message}
    inbox = open_im_inbox(store_id, shop_id=shop_id, wait=wait)
    if not inbox.get("ok"):
        return {
            "ok": False,
            "error": "无法打开私信页",
            "detail": from_detail,
            "new_message": new_message,
            "inbox": inbox,
        }
    clicked = search_and_open_conversation(
        store_id, name, creator_id=cid, wait=wait
    )
    if not clicked.get("ok"):
        return {
            "ok": False,
            "error": "私信中找不到会话",
            "inbox": inbox,
            "click": clicked,
        }
    return {"ok": True, "via": "inbox-search", "inbox": inbox, "click": clicked}


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
    return bool(predicate(im_thread_text(probe)))
