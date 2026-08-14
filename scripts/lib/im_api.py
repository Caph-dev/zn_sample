#!/usr/bin/env python3
"""页面内 TikTok IM SDK 的窄发送适配器。

TikTok 联盟私信发送不是普通 JSON HTTP 写接口，而是由页面内 IM SDK
通过 WebSocket 管理发送。此模块只调用当前会话已经加载的文本发送回调，
不点击发送按钮、不读取 Cookie 或 token，也不提供任意 SDK 方法代理。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .zclaw import zclaw_exec

IM_SDK_SEND_STATE_KEY = "__znImSdkSendRequests"
MAX_MESSAGE_LENGTH = 2000

SEND_TEXT_VIA_SDK_JS_TMPL = r"""
((requestId, body, expectedCreatorName) => {
  const stateKey = '__znImSdkSendRequests';
  const stateMap = window[stateKey] || (window[stateKey] = {});
  const baseState = {
    request_id: requestId,
    started: false,
    creator_name: expectedCreatorName,
  };
  stateMap[requestId] = baseState;

  if (!/\/seller\/im(?:[/?#]|$)/.test(location.pathname)) {
    return JSON.stringify({ok: false, reason: 'not-on-im-page'});
  }
  const pageText = String(document.body && document.body.innerText || '').toLowerCase();
  if (expectedCreatorName && !pageText.includes(expectedCreatorName.toLowerCase())) {
    return JSON.stringify({ok: false, reason: 'target-conversation-not-visible'});
  }

  const textarea = document.querySelector('textarea');
  const fiberKey = textarea && Object.keys(textarea).find(key =>
    key.startsWith('__reactFiber') || key.startsWith('__reactInternalInstance')
  );
  let fiber = fiberKey ? textarea[fiberKey] : null;
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
})(%REQUEST_ID%, %BODY%, %CREATOR_NAME%)
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
