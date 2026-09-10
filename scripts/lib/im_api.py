#!/usr/bin/env python3
"""页面内 TikTok IM SDK 的窄发送适配器。

TikTok 联盟私信发送不是普通 JSON HTTP 写接口，而是由页面内 IM SDK
通过 WebSocket 管理发送。此模块只调用当前会话已经加载的文本发送回调与
图片发送回调，不点击发送按钮、不读取 Cookie 或 token，也不提供任意
SDK 方法代理。图片走 SDK 的 sendImageMessageWithFiles（内部 multipart
上传 images[]），不走 DOM 上传。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .zclaw import zclaw_exec

IM_SDK_SEND_STATE_KEY = "__znImSdkSendRequests"
MAX_MESSAGE_LENGTH = 2000

IM_IMAGE_CHUNK_STATE_KEY = "__znImImageUploadChunks"
IM_IMAGE_SEND_STATE_KEY = "__znImImageSendRequests"
DEFAULT_IMAGE_CHUNK_CHARS = 60_000
# 本地安全上限（不是平台限制）：避免误传超大文件把 Bridge 刷爆。
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_SEND_POLL_ATTEMPTS = 6
IMAGE_SEND_POLL_INTERVAL_SEC = 2.0

_FIND_IM_SDK_PROVIDER_JS = r"""
  const findImSdkProvider = () => {
    const rootElement = document.querySelector('#root') || document.querySelector('#app') || document.body;
    const containerKey = Object.keys(rootElement).find(key =>
      key.startsWith('__reactContainer') || key.startsWith('__reactFiber'));
    let fiber = containerKey ? rootElement[containerKey] : null;
    if (fiber && fiber.current) fiber = fiber.current;
    const stack = [fiber];
    let visited = 0;
    while (stack.length && visited < 20000) {
      const node = stack.pop();
      if (!node || visited >= 20000) continue;
      visited += 1;
      const props = node.memoizedProps;
      const value = props && typeof props === 'object' ? props.value : null;
      if (value && typeof value === 'object'
          && typeof value.sendImageMessageWithFiles === 'function') {
        return {value: value, visited: visited};
      }
      if (node.child) stack.push(node.child);
      if (node.sibling) stack.push(node.sibling);
    }
    return {value: null, visited: visited};
  };
"""

PROBE_IMAGE_SDK_JS = (
    r"""
(() => {
%FIND_PROVIDER%
  const found = findImSdkProvider();
  if (!found.value) {
    return JSON.stringify({ok: false, reason: 'im-sdk-provider-not-found', visited: found.visited});
  }
  const value = found.value;
  return JSON.stringify({
    ok: true,
    visited: found.visited,
    has_send_image: typeof value.sendImageMessageWithFiles === 'function',
    has_sdk_instance: !!value.sdkInstance,
    sdk_status: (value.sdkStatus === null || value.sdkStatus === undefined)
      ? null : String(value.sdkStatus),
    context_keys: Object.keys(value).slice(0, 40),
  });
})()
"""
).replace("%FIND_PROVIDER%", _FIND_IM_SDK_PROVIDER_JS)

APPEND_IMAGE_CHUNK_JS_TMPL = r"""
((stateKey, index, chunk) => {
  const state = window[stateKey] || (window[stateKey] = {chunks: []});
  state.chunks[index] = chunk;
  return JSON.stringify({ok: true, index: index, received: state.chunks.length});
})(%STATE_KEY%, %INDEX%, %CHUNK%)
"""

SEND_IMAGE_VIA_SDK_JS_TMPL = (
    r"""
((stateKey, sendStateKey, requestId, fileName, mimeType, conversationId, execute) => {
%FIND_PROVIDER%
  const upload = window[stateKey];
  if (!upload || !Array.isArray(upload.chunks) || !upload.chunks.length) {
    return JSON.stringify({ok: false, reason: 'image-chunks-missing'});
  }
  let file;
  try {
    const binary = atob(upload.chunks.join(''));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    file = new File([bytes], fileName, {type: mimeType});
  } catch (error) {
    return JSON.stringify({ok: false, reason: 'image-decode-failed'});
  }
  try { delete window[stateKey]; } catch (error) {}

  const found = findImSdkProvider();
  const provider = found.value;
  if (!provider) {
    return JSON.stringify({ok: false, reason: 'im-sdk-provider-not-found', visited: found.visited});
  }
  if (!provider.sdkInstance) {
    return JSON.stringify({ok: false, reason: 'im-sdk-not-ready', sdk_status: String(provider.sdkStatus)});
  }
  const sendRequests = window[sendStateKey] || (window[sendStateKey] = {});
  const state = {
    started: false,
    resolved: false,
    file_size: file.size,
    conversation_id: String(conversationId || ''),
  };
  sendRequests[requestId] = state;
  if (!execute) {
    state.resolved = true;
    state.dry_run = true;
    return JSON.stringify({
      ok: true,
      request_id: requestId,
      state: 'dry-run-file-assembled',
      file_size: file.size,
      visited: found.visited,
    });
  }
  if (!state.conversation_id) {
    return JSON.stringify({ok: false, reason: 'missing-conversation-id'});
  }
  try {
    const result = provider.sendImageMessageWithFiles(state.conversation_id, [file]);
    state.started = true;
    state.return_type = typeof result;
    if (result && typeof result.then === 'function') {
      result.then(
        () => { state.resolved = true; },
        error => { state.error = String(error); state.resolved = true; }
      );
    }
    return JSON.stringify({
      ok: true,
      request_id: requestId,
      state: 'sdk-image-send-started',
      file_size: file.size,
      return_type: state.return_type,
    });
  } catch (error) {
    state.error = String(error);
    return JSON.stringify({ok: false, reason: String(error)});
  }
})(%STATE_KEY%, %SEND_STATE_KEY%, %REQUEST_ID%, %FILE_NAME%, %MIME_TYPE%, %CONVERSATION_ID%, %EXECUTE%)
"""
).replace("%FIND_PROVIDER%", _FIND_IM_SDK_PROVIDER_JS)

POLL_IMAGE_SEND_STATE_JS_TMPL = r"""
((sendStateKey, requestId) => {
  const requests = window[sendStateKey] || {};
  const state = requests[requestId] || null;
  return JSON.stringify({ok: !!state, state: state});
})(%SEND_STATE_KEY%, %REQUEST_ID%)
"""

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
  // 与 composer_identity_matches 对齐：只认输入框 fiber 身份。
  // ID 精确或 handle 精确；读不到身份就拒发，不用选中卡/正文子串。
  const idMatched = !!(expectedId && currentUserId && expectedId === currentUserId);
  const nameMatched = !!(want && currentScreenName && currentScreenName === want);
  if (!idMatched && !nameMatched) {
    return JSON.stringify({ok: false, reason: 'target-conversation-not-visible'});
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


def image_bytes_to_chunks(
    payload: bytes,
    *,
    chunk_chars: int = DEFAULT_IMAGE_CHUNK_CHARS,
) -> list[str]:
    """Split base64 image payload for staged injection into the page context."""
    encoded = base64.b64encode(payload).decode("ascii")
    size = max(1000, int(chunk_chars))
    return [encoded[index : index + size] for index in range(0, len(encoded), size)]


def probe_image_sdk(store_id: str, *, timeout: int = 60) -> dict[str, Any]:
    """Read-only check for the IM SDK image message provider."""
    result = zclaw_exec(store_id, PROBE_IMAGE_SDK_JS, timeout=timeout, retries=1)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "bad-image-probe-result", "raw": str(result)[:200]}
    return result


def send_image_message_via_sdk(
    store_id: str,
    conversation_id: str,
    image_path: str | Path,
    *,
    expected_creator_name: str = "",
    expected_creator_id: str = "",
    execute: bool = False,
    assemble_file: bool | None = None,
    chunk_chars: int = DEFAULT_IMAGE_CHUNK_CHARS,
    poll_attempts: int = IMAGE_SEND_POLL_ATTEMPTS,
    poll_interval_sec: float = IMAGE_SEND_POLL_INTERVAL_SEC,
) -> dict[str, Any]:
    """Send one image through the in-page IM SDK; never falls back to DOM upload.

    ``execute=False`` (default) only probes the provider. Pass
    ``assemble_file=True`` to additionally build the File in the page without
    sending, which validates the chunked injection path.
    """
    resolved_path = Path(image_path)
    if not resolved_path.is_file():
        return {"ok": False, "reason": "image-not-found", "path": str(resolved_path)}
    payload = resolved_path.read_bytes()
    if len(payload) > MAX_IMAGE_BYTES:
        return {
            "ok": False,
            "reason": "image-too-large",
            "bytes": len(payload),
            "max_bytes": MAX_IMAGE_BYTES,
        }
    if not execute and not assemble_file:
        return {
            "ok": True,
            "status": "dry-run",
            "file_bytes": len(payload),
            "capability": probe_image_sdk(store_id),
        }
    if execute and not str(conversation_id or "").strip():
        return {"ok": False, "reason": "missing-conversation-id"}

    for index, chunk in enumerate(image_bytes_to_chunks(payload, chunk_chars=chunk_chars)):
        push_script = (
            APPEND_IMAGE_CHUNK_JS_TMPL
            .replace("%STATE_KEY%", _js_string(IM_IMAGE_CHUNK_STATE_KEY))
            .replace("%INDEX%", str(index))
            .replace("%CHUNK%", _js_string(chunk))
        )
        pushed = zclaw_exec(store_id, push_script, timeout=60, retries=0)
        if not isinstance(pushed, dict) or not pushed.get("ok"):
            return {
                "ok": False,
                "reason": "image-chunk-push-failed",
                "index": index,
                "raw": str(pushed)[:200],
            }

    request_id = uuid.uuid4().hex
    send_script = (
        SEND_IMAGE_VIA_SDK_JS_TMPL
        .replace("%STATE_KEY%", _js_string(IM_IMAGE_CHUNK_STATE_KEY))
        .replace("%SEND_STATE_KEY%", _js_string(IM_IMAGE_SEND_STATE_KEY))
        .replace("%REQUEST_ID%", _js_string(request_id))
        .replace("%FILE_NAME%", _js_string(resolved_path.name))
        .replace("%MIME_TYPE%", _js_string(_image_mime_type(resolved_path)))
        .replace("%CONVERSATION_ID%", _js_string(str(conversation_id or "")))
        .replace("%EXECUTE%", "true" if execute else "false")
    )
    result = zclaw_exec(store_id, send_script, timeout=120, retries=0)
    if not isinstance(result, dict):
        return {
            "ok": False,
            "reason": "bad-image-send-result",
            "raw": str(result)[:200],
            "request_id": request_id,
        }
    result["request_id"] = request_id
    if expected_creator_name:
        result["expected_creator_name"] = str(expected_creator_name)
    if expected_creator_id:
        result["expected_creator_id"] = str(expected_creator_id)
    if result.get("ok") and execute and result.get("state") == "sdk-image-send-started":
        upload_state = _poll_image_send_state(
            store_id,
            request_id,
            attempts=poll_attempts,
            interval_sec=poll_interval_sec,
        )
        result["upload_state"] = upload_state
        state = upload_state.get("state") if isinstance(upload_state, dict) else None
        state = state if isinstance(state, dict) else {}
        if state.get("error"):
            result["ok"] = False
            result["reason"] = "image-send-failed"
            result["error"] = str(state.get("error"))[:240]
        elif state.get("started") and state.get("resolved"):
            result["send_postcheck"] = "confirmed"
        else:
            result["send_postcheck"] = "unknown"
    return result


def _poll_image_send_state(
    store_id: str,
    request_id: str,
    *,
    attempts: int,
    interval_sec: float,
) -> dict[str, Any]:
    script = (
        POLL_IMAGE_SEND_STATE_JS_TMPL
        .replace("%SEND_STATE_KEY%", _js_string(IM_IMAGE_SEND_STATE_KEY))
        .replace("%REQUEST_ID%", _js_string(request_id))
    )
    total_attempts = max(1, int(attempts))
    latest: dict[str, Any] = {}
    for attempt in range(total_attempts):
        try:
            result = zclaw_exec(store_id, script, timeout=30, retries=0)
        except Exception as error:
            return {"ok": False, "error": str(error)[:240], "attempts": attempt + 1}
        if isinstance(result, dict):
            latest = result
            state = result.get("state") if isinstance(result.get("state"), dict) else {}
            if state.get("resolved"):
                break
        if attempt + 1 < total_attempts:
            time.sleep(max(0.0, float(interval_sec)))
    return latest


def _image_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "image/png"


_FINGERPRINT_STRIP_RE = re.compile(r"[^0-9a-záéíóúüñ ]+")


def _normalized_message_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"\s+", " ", _FINGERPRINT_STRIP_RE.sub(" ", normalized)).strip()


def message_text_fingerprint(body: str, *, length: int = 60) -> str:
    """Normalized leading fragment used to confirm a sent message in-thread.

    Drops a leading ``Hi``/``Hola`` greeting plus the variable creator name so
    the fingerprint stays stable across renderings of the same template.
    """
    compact = _normalized_message_text(body)
    if not compact:
        return ""
    tokens = compact.split(" ")
    first = tokens[0]
    if first in {"hi", "hola"}:
        compact = " ".join(tokens[2:]) if len(tokens) > 2 else " ".join(tokens[1:])
    elif first.startswith("hola") and len(first) > 4:
        # The Spanish arrival template glues the greeting to the name ("Holaana").
        compact = " ".join(tokens[1:])
    elif first.startswith("hi") and len(first) > 2:
        compact = " ".join(tokens[1:])
    return compact[:length]


def thread_contains_message_predicate(body: str) -> Callable[[str], bool]:
    """Return a predicate that checks the rendered message inside a thread."""
    fingerprint = message_text_fingerprint(body)

    def predicate(thread_text: str) -> bool:
        return bool(fingerprint) and fingerprint in _normalized_message_text(thread_text)

    return predicate


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
    image_path: str | Path | None = None,
    image_chunk_chars: int = DEFAULT_IMAGE_CHUNK_CHARS,
) -> dict[str, Any]:
    """从样品申请页新消息路径打开会话，再可选发送一条文本（+可选一张图）。

    Default is dry-run: open the conversation and inspect it, but never call
    the IM SDK or click the send button. ``execute=True`` is required to send.
    ``image_path`` 只在文本确认发送后、且 ``write_source="api"`` 时追加发送；
    ``shop_id`` 仅为兼容既有调用方保留；打开会话不使用它，也不导航到其它页面。
    """
    from .im_dom import (
        composer_identity_matches,
        fill_or_send_message,
        im_thread_text_with_tail,
        inspect_current_thread,
        open_conversation_via_new_message,
    )

    name = str(creator_name or "").strip()
    normalized_body = str(body or "").strip()
    image_planned = bool(image_path)
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
    thread_text = im_thread_text_with_tail(probe)
    if already_sent_predicate is not None and already_sent_predicate(thread_text):
        return {
            "ok": True,
            "status": "already-sent",
            "message": normalized_body,
            "image_planned": image_planned,
            "image_skipped": "already-sent" if image_planned else "",
        }
    if not execute:
        return {
            "ok": True,
            "status": "dry-run",
            "message": normalized_body,
            "image_planned": image_planned,
        }

    if not composer_identity_matches(probe, name, resolved_creator_id):
        return {
            "ok": False,
            "error": "target-conversation-not-visible",
            "message": normalized_body,
        }

    if write_source == "api":
        click_detail = (
            clicked.get("click") if isinstance(clicked.get("click"), dict) else clicked
        )
        conversation_id = str((click_detail or {}).get("conversation_id") or "")
        sent = send_message_via_sdk(
            store_id,
            normalized_body,
            expected_creator_name=name,
            expected_creator_id=resolved_creator_id,
            conversation_id=conversation_id,
        )
        if not sent.get("ok"):
            return {
                "ok": False,
                "error": sent.get("reason") or str(sent),
                "message": normalized_body,
                "send_source": write_source,
                "image": None,
            }
        time.sleep(max(1.5, wait))
        post_probe = inspect_current_thread(store_id, name, wait=wait)
        confirmed = True
        if already_sent_predicate is not None:
            confirmed = already_sent_predicate(im_thread_text_with_tail(post_probe))
        image_result: dict[str, Any] | None = None
        if confirmed and image_path:
            image_result = send_image_message_via_sdk(
                store_id,
                conversation_id,
                image_path,
                expected_creator_name=name,
                expected_creator_id=resolved_creator_id,
                execute=True,
                chunk_chars=image_chunk_chars,
            )
        return {
            "ok": confirmed,
            "status": "sent" if confirmed else "send-unknown",
            "message": normalized_body,
            "send_source": write_source,
            "send_postcheck": "confirmed" if confirmed else "unknown",
            "error": "API 已启动但发送后未确认，禁止自动重试" if not confirmed else "",
            "image": image_result,
        }

    sent = fill_or_send_message(store_id, normalized_body, execute=True)
    if sent.get("ok") and sent.get("sent"):
        time.sleep(1.0)
        post_probe = inspect_current_thread(store_id, name, wait=wait)
        confirmed = True
        if already_sent_predicate is not None:
            confirmed = already_sent_predicate(im_thread_text_with_tail(post_probe))
        return {
            "ok": confirmed,
            "status": "sent" if confirmed else "send-unknown",
            "message": normalized_body,
            "send_source": write_source,
            "send_postcheck": "confirmed" if confirmed else "unknown",
            "error": "DOM 点击后未确认消息，禁止自动重试" if not confirmed else "",
            "image": None,
            "image_error": (
                "dom-write-source-has-no-image-support" if image_planned else ""
            ),
        }
    return {
        "ok": False,
        "error": sent.get("error") or str(sent),
        "message": normalized_body,
        "send_source": write_source,
        "image": None,
    }
