#!/usr/bin/env python3
"""联盟「达人消息」：严格从样品申请页的新消息路径打开会话。

默认只读。真正点「发送」仅当调用方传入 execute=True。
禁止点「邀请」。
"""
from __future__ import annotations

import json
import time
from typing import Any

from .zclaw import zclaw_exec

NEW_MESSAGE_CONFIRM_TIMEOUT_SECONDS = 15.0
NEW_MESSAGE_CONFIRM_POLL_INTERVAL_SECONDS = 0.75
NEW_MESSAGE_CONFIRM_INSPECT_TIMEOUT_SECONDS = 15
SAMPLE_REQUEST_PATH = "/affiliate/sample/sample-request"

INSPECT_IM_JS = r"""
(() => {
  const text = ((document.body && document.body.innerText) || '').replace(/\u00a0/g, ' ');
  const input = document.querySelector('textarea, [placeholder*="发送消息"], [placeholder*="Send a message"]');
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
  const identityFromValue = value => {
    if (!value || typeof value !== 'object') return null;
    const source = value.userInfo && typeof value.userInfo === 'object'
      ? value.userInfo
      : value;
    const userId = String(source.userId || source.creatorId || source.creator_oecuid || '').trim();
    const conversationId = String(source.conversationId || '').trim();
    const screenName = String(source.screenName || source.handle || source.nickname || '').trim();
    if (!userId && !conversationId && !screenName) return null;
    return {userId, conversationId, screenName};
  };
  const identityFromFiber = element => {
    const fiberKey = Object.keys(element || {}).find(key =>
      key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
    );
    let fiber = fiberKey ? element[fiberKey] : null;
    for (let depth = 0; depth < 16 && fiber; depth++, fiber = fiber.return) {
      const props = fiber.memoizedProps || {};
      for (const candidate of [props.userInfo, props.contact, props.currentUser, props.currentConversation]) {
        const identity = identityFromValue(candidate);
        if (identity && (identity.userId || identity.screenName)) return identity;
      }
    }
    return null;
  };
  const currentIdentity = identityFromFiber(input);
  return JSON.stringify({
    href: location.href,
    title: document.title || '',
    hasComposer: !!(input || /发送消息|0\/2000/.test(text)),
    current_user_id: currentIdentity ? currentIdentity.userId : '',
    current_conversation_id: currentIdentity ? currentIdentity.conversationId : '',
    current_screen_name: currentIdentity ? currentIdentity.screenName : '',
    selected_preview: selected ? String(selected.innerText || '').slice(0, 200) : '',
    selected_user_id: selectedUserId,
    selected_conversation_id: selectedConversationId,
    thread_text: threadText.slice(0, 4000)
  });
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
  const unwrapField = value => {
    if (value && typeof value === 'object' && 'value' in value) return value.value;
    return value;
  };
  const resultIdentity = element => {
    const fiberKey = Object.keys(element).find(key =>
      key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
    );
    let fiber = fiberKey ? element[fiberKey] : null;
    for (let depth = 0; depth < 16 && fiber; depth++, fiber = fiber.return) {
      const props = fiber.memoizedProps || {};
      const dataSource = props.dataSource;
      const item = Array.isArray(dataSource) ? dataSource[0] : dataSource;
      if (!item || typeof item !== 'object') continue;
      const creatorId = String(unwrapField(item.creator_oecuid) || '').trim();
      const handle = String(unwrapField(item.handle) || '').trim();
      const nickname = String(unwrapField(item.nickname) || '').trim();
      if (creatorId || handle || nickname) {
        return {creatorId, handle, nickname};
      }
    }
    return {creatorId: '', handle: '', nickname: ''};
  };
  let row = null;
  let fallbackRow = null;
  let identity = {creatorId: '', handle: '', nickname: ''};
  let fallbackIdentity = {creatorId: '', handle: '', nickname: ''};
  for (const candidate of [...drawer.querySelectorAll('div')]) {
    const cls = String(candidate.className || '');
    if (!/hover:bg-state-hover/.test(cls) || candidate.getBoundingClientRect().width <= 0) {
      continue;
    }
    const candidateIdentity = resultIdentity(candidate);
    const candidateText = (candidate.innerText || '').toLowerCase();
    const identityMatches = [
      candidateIdentity.creatorId,
      candidateIdentity.handle,
      candidateIdentity.nickname,
    ].some(value => value && value.toLowerCase() === query);
    if (identityMatches) {
      row = candidate;
      identity = candidateIdentity;
      break;
    }
    if (!fallbackRow && candidateText.includes(query)) {
      fallbackRow = candidate;
      fallbackIdentity = candidateIdentity;
    }
  }
  if (!row && fallbackRow) {
    row = fallbackRow;
    identity = fallbackIdentity;
  }
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
      return JSON.stringify({
        ok: true,
        via: 'react-onClick',
        depth,
        search_key: query,
        result_creator_id: identity.creatorId,
        result_creator_name: identity.handle,
        result_nickname: identity.nickname,
      });
    }
  }
  btn.click();
  return JSON.stringify({
    ok: true,
    via: 'native',
    search_key: query,
    result_creator_id: identity.creatorId,
    result_creator_name: identity.handle,
    result_nickname: identity.nickname,
  });
})(%WANT%)
"""


def _js_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def inspect_im(
    store_id: str,
    *,
    timeout: int = 30,
    deadline: float | None = None,
) -> dict[str, Any]:
    ret = zclaw_exec(
        store_id,
        INSPECT_IM_JS,
        timeout=timeout,
        retries=0,
        deadline=deadline,
    )
    return ret if isinstance(ret, dict) else {"ok": False, "raw": ret}


def is_sample_request_page(probe: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(probe, dict)
        and SAMPLE_REQUEST_PATH in str(probe.get("href") or "")
    )


def composer_ready(probe: dict[str, Any] | None) -> bool:
    return bool(isinstance(probe, dict) and probe.get("hasComposer"))


def composer_identity_matches(
    probe: dict[str, Any] | None,
    creator_name: str,
    creator_id: str = "",
) -> bool:
    """发送前只核对 composer 身份：ID 精确或 handle 精确。

    读不到 composer 身份就失败。不要用选中卡、会话正文或整页文字做子串兜底；
    那些位置可能出现目标名字，但输入框仍停在上一个会话。
    """
    if not isinstance(probe, dict):
        return False
    name = (creator_name or "").strip().lower()
    cid = str(creator_id or "").strip()
    current_id = str(probe.get("current_user_id") or "").strip()
    current_name = str(probe.get("current_screen_name") or "").strip().lower()
    if cid and current_id and cid == current_id:
        return True
    if name and current_name == name:
        return True
    return False


def conversation_matches(
    probe: dict[str, Any] | None,
    creator_name: str,
    creator_id: str = "",
) -> bool:
    """用当前 composer/选中会话身份核对达人，不用整页正文。"""
    if composer_identity_matches(probe, creator_name, creator_id):
        return True
    if not isinstance(probe, dict):
        return False
    name = (creator_name or "").strip().lower()
    cid = str(creator_id or "").strip()
    selected_id = str(probe.get("selected_user_id") or "").strip()
    selected_preview = str(probe.get("selected_preview") or "").lower()
    selected_name = next(iter(selected_preview.splitlines()), "").strip()
    if cid and selected_id and cid == selected_id:
        return True
    if name and selected_name == name:
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


def _wait_for_new_message_conversation(
    store_id: str,
    *,
    creator_name: str,
    creator_id: str,
    wait: float,
) -> dict[str, Any]:
    """Poll until the new-message click has switched the composer identity.

    The result button closes the drawer synchronously, but the IM SDK updates
    the composer props and message list asynchronously. A single immediate
    probe can therefore still describe the previous conversation.
    """
    deadline = time.monotonic() + NEW_MESSAGE_CONFIRM_TIMEOUT_SECONDS
    probe: dict[str, Any] = {}
    probe_count = 0
    last_error = ""
    time.sleep(max(0.5, min(float(wait), 2.0)))
    while True:
        probe_count += 1
        try:
            candidate = inspect_im(
                store_id,
                timeout=NEW_MESSAGE_CONFIRM_INSPECT_TIMEOUT_SECONDS,
                deadline=deadline,
            )
            if isinstance(candidate, dict):
                probe = candidate
                if (
                    is_sample_request_page(probe)
                    and composer_ready(probe)
                    and conversation_matches(
                        probe,
                        creator_name,
                        creator_id,
                    )
                ):
                    return {
                        "ok": True,
                        "probe": probe,
                        "poll_count": probe_count,
                    }
        except Exception as error:
            last_error = str(error)[:240]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(NEW_MESSAGE_CONFIRM_POLL_INTERVAL_SECONDS, remaining))
    return {
        "ok": False,
        "probe": probe,
        "poll_count": probe_count,
        "sample_request_page": is_sample_request_page(probe),
        "error": last_error,
    }


def _try_open_via_new_message(
    store_id: str,
    creator_key: str,
    *,
    creator_name: str,
    creator_id: str,
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
    result_creator_id = str(clicked.get("result_creator_id") or "").strip()
    expected_creator_id = str(creator_id or result_creator_id).strip()
    verification_name = str(
        creator_name
        or clicked.get("result_creator_name")
        or clicked.get("result_nickname")
        or name
    ).strip()
    confirmation = _wait_for_new_message_conversation(
        store_id,
        creator_name=verification_name,
        creator_id=expected_creator_id,
        wait=wait,
    )
    probe = confirmation.get("probe") if isinstance(confirmation.get("probe"), dict) else {}
    if not confirmation.get("ok"):
        return {
            "ok": False,
            "error": "会话未确认切换" if composer_ready(probe) else "会话发送框未出现",
            "expected_creator_id": expected_creator_id,
            "click": clicked,
            "im": probe,
            "confirmation": confirmation,
        }
    return {
        "ok": True,
        "via": "new-message",
        "panel": opened_panel,
        "drawer": opened_drawer,
        "fill": filled,
        "click": clicked,
        "im": probe,
        "confirmation": confirmation,
    }


def open_conversation_via_new_message(
    store_id: str,
    creator_id: str = "",
    creator_name: str = "",
    *,
    wait: float = 2.0,
) -> dict[str, Any]:
    """严格按样品申请页「聊天数」→「发送消息」→达人 ID→「聊天」打开会话。

    实测链路（2026-08-28）：entryWrapper 弹面板 → 面板标题旁
    ``arco-icon-edit`` 按钮开抽屉 → 抽屉 ``input.core-input`` 填达人
    ID/昵称回车 → 结果行「聊天」按钮（React ``onClick``）打开会话。
    当前页面必须已经是样品申请页；不打开详情消息弹层、不跳转到
    ``/seller/im``、不点「聊天数」里的最近联系人。

    优先用 creator_id 搜索；平台搜索不接受数字 ID 时，再在同一个「新消息」
    抽屉里用 creator_name 搜索。无论使用哪个关键词，点击后都必须在当前
    输入框的 React 身份或选中卡里确认命中目标达人。
    """
    creator_id = str(creator_id or "").strip()
    creator_name = str(creator_name or "").strip()
    candidates = list(dict.fromkeys(key for key in (creator_id, creator_name) if key))
    name = creator_name
    if not candidates:
        return {"ok": False, "error": "empty-creator"}
    page_probe = inspect_im(store_id)
    current_href = str(page_probe.get("href") or "")
    if SAMPLE_REQUEST_PATH not in current_href:
        return {
            "ok": False,
            "error": "必须停在样品申请页",
            "href": current_href[:200],
            "im": page_probe,
        }
    failures: list[dict[str, Any]] = []
    for candidate in candidates:
        attempt = _try_open_via_new_message(
            store_id,
            candidate,
            creator_name=name,
            creator_id=creator_id,
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
