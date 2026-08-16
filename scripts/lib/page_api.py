#!/usr/bin/env python3
"""紫鸟页面上下文中的 TikTok 只读 API 传输层。

请求始终由当前紫鸟店铺页面发出，保留该页面的登录态、网络出口和浏览器环境。
本模块不读取、不导出 Cookie，也不提供任意写接口。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any

from .console import verbose_print
from .zclaw import DEFAULT_EXEC_RETRIES, zclaw_exec

AFFILIATE_HOST = "affiliate.tiktokshopglobalselling.com"
SAMPLE_LIST_ENDPOINT = "/api/v1/affiliate/sample/group/list"
CREATOR_PROFILE_ENDPOINT = "/api/v1/oec/affiliate/creator/marketplace/profile"
SAMPLE_GROUP_ACTION_ENDPOINT = "/api/v1/affiliate/sample/group/action"
READ_POST_ENDPOINTS = frozenset(
    {
        SAMPLE_LIST_ENDPOINT,
        CREATOR_PROFILE_ENDPOINT,
    }
)
WRITE_POST_ENDPOINTS = frozenset({SAMPLE_GROUP_ACTION_ENDPOINT})
SELLER_LOGISTICS_ENDPOINT = "/api/v1/fulfillment/na/logistic_detail/list"
SELLER_READ_GET_ENDPOINTS = frozenset({SELLER_LOGISTICS_ENDPOINT})
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


class PageApiError(RuntimeError):
    """页面上下文 API 请求失败。"""


class PageApiSchemaError(PageApiError):
    """API 响应结构不符合已验证契约。"""


@dataclass(frozen=True)
class AffiliatePageContext:
    href: str
    shop_id: str
    shop_region: str


@dataclass(frozen=True)
class SellerPageContext:
    href: str
    shop_id: str
    shop_region: str


def get_affiliate_page_context(store_id: str) -> AffiliatePageContext:
    """读取当前页面 URL 中 API 所需的店铺上下文，不读取会话信息。"""
    result = zclaw_exec(
        store_id,
        "(() => JSON.stringify({href: location.href || ''}))()",
        retries=DEFAULT_EXEC_RETRIES,
    )
    if not isinstance(result, dict):
        raise PageApiError(f"无法读取当前页面 URL: {result!r}"[:300])

    href = str(result.get("href") or "").strip()
    parsed = urllib.parse.urlsplit(href)
    if parsed.hostname != AFFILIATE_HOST or "sample-request" not in parsed.path:
        raise PageApiError(
            "当前页面不是 TikTok Shop 样品申请页；请手动停在「样品申请 → 待审核」"
        )

    query = urllib.parse.parse_qs(parsed.query)
    shop_id = str((query.get("shop_id") or [""])[0]).strip()
    shop_region = str((query.get("shop_region") or [""])[0]).strip().upper()
    if not shop_id:
        raise PageApiError(f"样品申请页 URL 缺少 shop_id: {href[:180]}")
    if not shop_region:
        raise PageApiError(f"样品申请页 URL 缺少 shop_region: {href[:180]}")
    return AffiliatePageContext(
        href=href,
        shop_id=shop_id,
        shop_region=shop_region,
    )


def get_seller_page_context(
    store_id: str,
    *,
    shop_id: str,
    shop_region: str = "US",
) -> SellerPageContext:
    """读取当前商家订单页上下文；店铺 ID 来自已读样品列表行。"""
    result = zclaw_exec(
        store_id,
        "(() => JSON.stringify({href: location.href || ''}))()",
        retries=DEFAULT_EXEC_RETRIES,
    )
    if not isinstance(result, dict):
        raise PageApiError(f"无法读取当前商家页面 URL: {result!r}"[:300])

    href = str(result.get("href") or "").strip()
    parsed = urllib.parse.urlsplit(href)
    hostname = str(parsed.hostname or "").lower()
    if not re.fullmatch(r"seller(?:\.[a-z0-9-]+)*\.tiktokshopglobalselling\.com", hostname):
        raise PageApiError("当前页面不是 TikTok Shop 商家中心订单页")
    if "/order" not in parsed.path:
        raise PageApiError("当前页面不是 TikTok Shop 商家订单页")

    normalized_shop_id = str(shop_id or "").strip()
    normalized_shop_region = str(shop_region or "US").strip().upper()
    if not normalized_shop_id:
        raise PageApiError("商家订单 API 缺少 shop_id")
    if not normalized_shop_region:
        raise PageApiError("商家订单 API 缺少 shop_region")
    return SellerPageContext(
        href=href,
        shop_id=normalized_shop_id,
        shop_region=normalized_shop_region,
    )


def _build_request_url(
    endpoint: str,
    *,
    context: AffiliatePageContext | SellerPageContext,
    allowed_endpoints: frozenset[str],
    extra_query: dict[str, Any] | None = None,
) -> str:
    if endpoint not in allowed_endpoints:
        raise PageApiError(f"未登记的页面 POST endpoint: {endpoint}")
    if isinstance(context, AffiliatePageContext):
        query: dict[str, str] = {
            "user_language": "zh-CN",
            "aid": "6556",
            "app_name": "i18n_ecom_alliance",
            "device_id": "0",
            "device_platform": "web",
            "oec_seller_id": context.shop_id,
            "shop_region": context.shop_region,
        }
    else:
        query = {
            "shop_id": context.shop_id,
            "shop_region": context.shop_region,
        }
    if extra_query:
        query.update(extra_query)
    return f"{endpoint}?{urllib.parse.urlencode(query, doseq=True)}"


def _build_read_url(
    endpoint: str,
    *,
    context: AffiliatePageContext,
) -> str:
    return _build_request_url(
        endpoint,
        context=context,
        allowed_endpoints=READ_POST_ENDPOINTS,
    )


def _post_page_json(
    store_id: str,
    endpoint: str,
    body: dict[str, Any],
    *,
    context: AffiliatePageContext | SellerPageContext,
    allowed_endpoints: frozenset[str],
    operation_label: str,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    retries: int = 0,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    start_exec_retries: int = 1,
    poll_exec_retries: int = 1,
    http_method: str = "POST",
    request_query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在页面上下文执行一个已登记的异步 POST。"""
    request_url = _build_request_url(
        endpoint,
        context=context,
        allowed_endpoints=allowed_endpoints,
        extra_query=request_query,
    )
    attempts = max(1, int(retries) + 1)
    last_error: PageApiError | None = None
    for attempt in range(attempts):
        request_id = uuid.uuid4().hex
        start_script = f"""
(() => {{
  const requestId = {json.dumps(request_id)};
  const requestUrl = {json.dumps(request_url, ensure_ascii=False)};
  const requestBody = {json.dumps(body, ensure_ascii=False)};
  const requestMethod = {json.dumps(http_method.upper())};
  const maxResponseBytes = {int(max_response_bytes)};
  const timeoutMilliseconds = {max(1000, int(request_timeout_seconds * 1000))};
  const stateKey = '__znPageApiReadRequests';
  const requestStates = window[stateKey] || (window[stateKey] = {{}});
  const abortController = new AbortController();
  const timeoutHandle = window.setTimeout(() => abortController.abort(), timeoutMilliseconds);
  requestStates[requestId] = {{done: false}};

  const requestOptions = {{
    method: requestMethod,
    headers: {{
      'accept': 'application/json, text/plain, */*',
      'content-type': 'application/json'
    }},
    signal: abortController.signal
  }};
  if (requestMethod !== 'GET' && requestMethod !== 'HEAD') {{
    requestOptions.body = JSON.stringify(requestBody);
  }}
  fetch(requestUrl, requestOptions).then(async response => {{
    const responseText = await response.text();
    const responseByteLength = new TextEncoder().encode(responseText).length;
    if (responseByteLength > maxResponseBytes) {{
      requestStates[requestId] = {{
        done: true,
        ok: false,
        error_type: 'response-too-large',
        status: response.status,
        response_length: responseByteLength
      }};
      return;
    }}
    let payload = null;
    try {{
      payload = JSON.parse(responseText);
    }} catch (error) {{
      requestStates[requestId] = {{
        done: true,
        ok: false,
        error_type: 'invalid-json',
        status: response.status,
        response_length: responseByteLength
      }};
      return;
    }}
    requestStates[requestId] = {{
      done: true,
      ok: response.ok,
      status: response.status,
      payload
    }};
  }}).catch(error => {{
    requestStates[requestId] = {{
      done: true,
      ok: false,
      error_type: error && error.name === 'AbortError' ? 'request-timeout' : 'request-exception',
      error: String(error)
    }};
  }}).finally(() => window.clearTimeout(timeoutHandle));

  return JSON.stringify({{ok: true, started: true, request_id: requestId}});
}})()
"""
        started_at = time.monotonic()
        try:
            start_result = zclaw_exec(
                store_id,
                start_script,
                timeout=30,
                retries=start_exec_retries,
            )
            if not isinstance(start_result, dict) or not start_result.get("started"):
                raise PageApiError(
                    f"{endpoint} 无法启动页面异步请求: {start_result!r}"[:400]
                )

            poll_script = f"""
(() => {{
  const requestId = {json.dumps(request_id)};
  const requestStates = window.__znPageApiReadRequests || {{}};
  const requestState = requestStates[requestId];
  if (!requestState) {{
    return JSON.stringify({{done: true, ok: false, error_type: 'request-state-missing'}});
  }}
  if (!requestState.done) return JSON.stringify({{done: false}});
  delete requestStates[requestId];
  return JSON.stringify(requestState);
}})()
"""
            deadline = time.monotonic() + max(1.0, request_timeout_seconds + 5.0)
            result: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                time.sleep(max(0.1, poll_interval_seconds))
                poll_result = zclaw_exec(
                    store_id,
                    poll_script,
                    timeout=30,
                    retries=poll_exec_retries,
                )
                if not isinstance(poll_result, dict):
                    raise PageApiError(
                        f"{endpoint} 轮询返回未知类型: {type(poll_result).__name__}"
                    )
                if poll_result.get("done"):
                    result = poll_result
                    break
            if result is None:
                raise PageApiError(f"{endpoint} 页面异步请求轮询超时")
        except Exception as error:
            last_error = (
                error
                if isinstance(error, PageApiError)
                else PageApiError(f"{endpoint} Bridge 请求失败: {error}")
            )
        else:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            if not result.get("ok"):
                last_error = PageApiError(
                    f"{endpoint} 请求失败 status={result.get('status')} "
                    f"type={result.get('error_type')} elapsed_ms={elapsed_ms}"
                )
            else:
                payload = result.get("payload")
                if not isinstance(payload, dict):
                    raise PageApiSchemaError(
                        f"{endpoint} payload 非对象: {type(payload).__name__}"
                    )
                business_code = payload.get("code")
                if business_code != 0:
                    raise PageApiError(
                        f"{endpoint} 业务失败 code={business_code} "
                        f"message={str(payload.get('message') or '')[:160]}"
                    )
                verbose_print(
                    f"[页面API] {operation_label} {http_method.upper()} {endpoint} "
                    f"status={result.get('status')} code={business_code} "
                    f"elapsed_ms={elapsed_ms}",
                    flush=True,
                )
                return payload

        if attempt + 1 < attempts:
            time.sleep(0.8 * (attempt + 1))

    assert last_error is not None
    raise last_error


def post_read_json(
    store_id: str,
    endpoint: str,
    body: dict[str, Any],
    *,
    context: AffiliatePageContext,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    retries: int = 2,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """在当前页面中执行已登记的查询型 POST，并返回业务 payload。"""
    return _post_page_json(
        store_id,
        endpoint,
        body,
        context=context,
        allowed_endpoints=READ_POST_ENDPOINTS,
        operation_label="只读",
        max_response_bytes=max_response_bytes,
        retries=retries,
        request_timeout_seconds=request_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        start_exec_retries=1,
        poll_exec_retries=1,
    )


def post_sample_group_action_json(
    store_id: str,
    body: dict[str, Any],
    *,
    context: AffiliatePageContext,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """发送已登记的样品批准动作；不自动重试，避免不确定状态下重复写入。"""
    return _post_page_json(
        store_id,
        SAMPLE_GROUP_ACTION_ENDPOINT,
        body,
        context=context,
        allowed_endpoints=WRITE_POST_ENDPOINTS,
        operation_label="批准",
        retries=0,
        request_timeout_seconds=request_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        start_exec_retries=0,
        poll_exec_retries=1,
    )


def get_seller_read_json(
    store_id: str,
    endpoint: str,
    *,
    query: dict[str, Any],
    context: SellerPageContext,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    retries: int = 2,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """在商家订单页执行已登记的物流查询 GET。"""
    return _post_page_json(
        store_id,
        endpoint,
        {},
        context=context,
        allowed_endpoints=SELLER_READ_GET_ENDPOINTS,
        operation_label="商家订单只读",
        max_response_bytes=max_response_bytes,
        retries=retries,
        request_timeout_seconds=request_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        start_exec_retries=1,
        poll_exec_retries=1,
        http_method="GET",
        request_query=query,
    )
