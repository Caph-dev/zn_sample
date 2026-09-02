#!/usr/bin/env python3
"""页面内 TikTok IM SDK 的窄发送适配器。

TikTok 联盟私信发送不是普通 JSON HTTP 写接口，而是由页面内 IM SDK
通过 WebSocket 管理发送。此模块只调用当前会话已经加载的文本发送回调，
不点击发送按钮、不读取 Cookie 或 token，也不提供任意 SDK 方法代理。
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from .zclaw import zclaw_exec

IM_SDK_SEND_STATE_KEY = "__znImSdkSendRequests"
MAX_MESSAGE_LENGTH = 2000

SEND_TEXT_VIA_SDK_JS_TMPL = r"""
((requestId, body, expectedCreatorName, expectedCreatorId) => {
  const stateKey = '__znImSdkSendRequests';
  const stateMap = window[stateKey] || (window[stateKey] = {});
  const baseState = {
    request_id: requestId,
    started: false,
    creator_name: expectedCreatorName,
  };
  stateMap[requestId] = baseState;

  const composer = document.querySelector('textarea, [placeholder*="发送消息"], [placeholder*="Send a message"]');
  if (!composer) {
    return JSON.stringify({ok: false, reason: 'not-in-new-message-conversation'});
  }
  const want = String(expectedCreatorName || '').trim().toLowerCase();
  const expectedId = String(expectedCreatorId || '').trim();
  const selectedCard = [...document.querySelectorAll('div')].find(el => {
    const cls = String(el.className || '');
    return /contactCard/.test(cls) && /selected/.test(cls);
  });
  const selectedText = selectedCard ? String(selectedCard.innerText || '').toLowerCase() : '';
  const identityFromValue = value => {
    if (!value || typeof value !== 'object') return null;
    const source = value.userInfo && typeof value.userInfo === 'object'
      ? value.userInfo
      : value;
    const userId = String(source.userId || source.creatorId || source.creator_oecuid || '').trim();
    const screenName = String(source.screenName || source.handle || source.nickname || '').trim();
    if (!userId && !screenName) return null;
    return {userId, screenName};
  };
  const fiberKey = Object.keys(composer).find(key =>
    key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
  );
  let fiber = fiberKey ? composer[fiberKey] : null;
  let currentIdentity = null;
  for (let depth = 0; depth < 16 && fiber; depth++, fiber = fiber.return) {
    const props = fiber.memoizedProps || {};
    for (const candidate of [props.userInfo, props.contact, props.currentUser, props.currentConversation]) {
      const identity = identityFromValue(candidate);
      if (identity && (identity.userId || identity.screenName)) {
        currentIdentity = identity;
        break;
      }
    }
    if (currentIdentity) break;
  }
  const currentUserId = currentIdentity ? currentIdentity.userId : '';
  const currentScreenName = currentIdentity ? currentIdentity.screenName.toLowerCase() : '';
  if (expectedId && currentUserId) {
    if (expectedId !== currentUserId) {
      return JSON.stringify({ok: false, reason: 'target-conversation-not-visible'});
    }
  } else if (want && currentScreenName) {
    if (currentScreenName !== want) {
      return JSON.stringify({ok: false, reason: 'target-conversation-not-visible'});
    }
  } else if (want && (!selectedText || !selectedText.includes(want))) {
    if (expectedId || !selectedText) {
      return JSON.stringify({ok: false, reason: 'target-conversation-not-visible'});
    }
  }

  const textarea = document.querySelector('textarea');
  const textareaFiberKey = textarea && Object.keys(textarea).find(key =>
    key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
  );
  fiber = textareaFiberKey ? textarea[textareaFiberKey] : null;
  let sendProps = null;
  for (let depth = 0; depth < 10 && fiber; depth++, fiber = fiber.return) {
    const props = fiber.memoizedProps;
    if (props && typeof props.onSendText === 'function') {
      sendProps = props;
      break;
    }
  }
  if (!sendProps) {
    return JSON.stringify({ok: false, reason: 'im-sdk-send-handler-not-found'});
  }
  if (!body || body.length > 2000) {
    return JSON.stringify({ok: false, reason: 'invalid-message-length'});
  }

  try {
    const result = sendProps.onSendText(body, {});
    baseState.started = true;
    baseState.return_type = typeof result;
    if (result && typeof result.then === 'function') {
      result.then(
        () => { baseState.resolved = true; },
        error => { baseState.error = String(error); }
      );
    }
    window.setTimeout(() => {
      try { delete stateMap[requestId]; } catch (error) {}
    }, 60000);
    return JSON.stringify({
      ok: true,
      request_id: requestId,
      state: 'sdk-send-started',
      return_type: typeof result,
    });
  } catch (error) {
    baseState.error = String(error);
    return JSON.stringify({ok: false, reason: String(error)});
  }
})(%REQUEST_ID%, %BODY%, %CREATOR_NAME%, %CREATOR_ID%)
"""


def _js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def send_message_via_sdk(
    store_id: str,
    body: str,
    *,
    expected_creator_name: str,
    expected_creator_id: str = "",
    conversation_id: str = "",
) -> dict[str, Any]:
    """通过当前 IM 页面已加载的 SDK 发送单条文本，不自动重试。"""
    normalized_body = str(body or "").strip()
    normalized_name = str(expected_creator_name or "").strip()
    if not normalized_body:
        return {"ok": False, "reason": "empty-message"}
    if len(normalized_body) > MAX_MESSAGE_LENGTH:
        return {"ok": False, "reason": "message-too-long"}
    if not normalized_name:
        return {"ok": False, "reason": "missing-creator-name"}

    request_id = uuid.uuid4().hex
    script = (
        SEND_TEXT_VIA_SDK_JS_TMPL
        .replace("%REQUEST_ID%", _js_string(request_id))
        .replace("%BODY%", _js_string(normalized_body))
        .replace("%CREATOR_NAME%", _js_string(normalized_name))
        .replace("%CREATOR_ID%", _js_string(str(expected_creator_id or "").strip()))
    )
    result = zclaw_exec(store_id, script, retries=0)
    if not isinstance(result, dict):
        return {
            "ok": False,
            "reason": "bad-sdk-send-result",
            "raw": str(result)[:200],
            "request_id": request_id,
        }
    result.setdefault("request_id", request_id)
    if expected_creator_id:
        result["expected_creator_id"] = str(expected_creator_id)
    if conversation_id:
        result["conversation_id"] = str(conversation_id)
    return result


def send_direct_message(
    store_id: str,
    body: str,
    *,
    creator_name: str,
    creator_id: str = "",
    shop_id: str = "",
    execute: bool = False,
    wait: float = 2.5,
    write_source: str = "api",
    already_sent_predicate: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """从样品申请页新消息路径打开会话，再可选发送一条文本。

    Default is dry-run: open the conversation and inspect it, but never call
    the IM SDK or click the send button. ``execute=True`` is required to send.
    ``shop_id`` 仅为兼容既有调用方保留；打开会话不使用它，也不导航到其它页面。
    """
    from .im_dom import (
        fill_or_send_message,
        im_thread_text,
        inspect_current_thread,
        open_conversation_via_new_message,
    )

    name = str(creator_name or "").strip()
    normalized_body = str(body or "").strip()
    opened = open_conversation_via_new_message(
        store_id,
        creator_id=str(creator_id or ""),
        creator_name=name,
        wait=wait,
    )
    if not opened.get("ok"):
        return {
            "ok": False,
            "error": opened.get("error") or "找不到会话",
            "message": normalized_body,
        }
    clicked = opened.get("click") if isinstance(opened.get("click"), dict) else {}
    resolved_creator_id = str(
        creator_id or clicked.get("result_creator_id") or ""
    ).strip()
    probe = inspect_current_thread(store_id, name, wait=wait)
    thread_text = im_thread_text(probe)
    if already_sent_predicate is not None and already_sent_predicate(thread_text):
        return {"ok": True, "status": "already-sent", "message": normalized_body}
    if not execute:
        return {"ok": True, "status": "dry-run", "message": normalized_body}

    if write_source == "api":
        click_detail = (
            clicked.get("click") if isinstance(clicked.get("click"), dict) else clicked
        )
        sent = send_message_via_sdk(
            store_id,
            normalized_body,
            expected_creator_name=name,
            expected_creator_id=resolved_creator_id,
            conversation_id=str((click_detail or {}).get("conversation_id") or ""),
        )
        if not sent.get("ok"):
            return {
                "ok": False,
                "error": sent.get("reason") or str(sent),
                "message": normalized_body,
                "send_source": write_source,
            }
        time.sleep(max(1.5, wait))
        post_probe = inspect_current_thread(store_id, name, wait=wait)
        confirmed = True
        if already_sent_predicate is not None:
            confirmed = already_sent_predicate(im_thread_text(post_probe))
        return {
            "ok": confirmed,
            "status": "sent" if confirmed else "send-unknown",
            "message": normalized_body,
            "send_source": write_source,
            "send_postcheck": "confirmed" if confirmed else "unknown",
            "error": "API 已启动但发送后未确认，禁止自动重试" if not confirmed else "",
        }

    sent = fill_or_send_message(store_id, normalized_body, execute=True)
    if sent.get("ok") and sent.get("sent"):
        time.sleep(1.0)
        post_probe = inspect_current_thread(store_id, name, wait=wait)
        confirmed = True
        if already_sent_predicate is not None:
            confirmed = already_sent_predicate(im_thread_text(post_probe))
        return {
            "ok": confirmed,
            "status": "sent" if confirmed else "send-unknown",
            "message": normalized_body,
            "send_source": write_source,
            "send_postcheck": "confirmed" if confirmed else "unknown",
            "error": "DOM 点击后未确认消息，禁止自动重试" if not confirmed else "",
        }
    return {
        "ok": False,
        "error": sent.get("error") or str(sent),
        "message": normalized_body,
        "send_source": write_source,
    }
