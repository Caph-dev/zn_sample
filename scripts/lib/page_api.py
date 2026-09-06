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
from dataclasses import dataclass, field
from typing import Any, Callable

from .console import verbose_print
from .operation_cancel import OperationCancelled, raise_if_cancelled
from .sync_errors import LogisticsRequestTimeout
from .time_budget import (
    deadline_from_timeout,
    remaining_seconds,
    sleep_until_next_probe,
)
from .zclaw import HREF_PROBE_TIMEOUT_SECONDS, zclaw_exec

AFFILIATE_HOST = "affiliate.tiktokshopglobalselling.com"
SAMPLE_LIST_ENDPOINT = "/api/v1/affiliate/sample/group/list"
SAMPLE_PERFORMANCE_ENDPOINT = "/api/v1/affiliate/sample/performance"
CREATOR_PROFILE_ENDPOINT = "/api/v1/oec/affiliate/creator/marketplace/profile"
SAMPLE_GROUP_ACTION_ENDPOINT = "/api/v1/affiliate/sample/group/action"
READ_POST_ENDPOINTS = frozenset(
    {
        SAMPLE_LIST_ENDPOINT,
        CREATOR_PROFILE_ENDPOINT,
    }
)
AFFILIATE_READ_GET_ENDPOINTS = frozenset({SAMPLE_PERFORMANCE_ENDPOINT})
WRITE_POST_ENDPOINTS = frozenset({SAMPLE_GROUP_ACTION_ENDPOINT})
SELLER_LOGISTICS_ENDPOINT = "/api/v1/fulfillment/na/logistic_detail/list"
SELLER_READ_GET_ENDPOINTS = frozenset({SELLER_LOGISTICS_ENDPOINT})
SELLER_AID = "6556"
SELLER_APP_NAME = "i18n_ecom_shop"
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


class PageApiError(RuntimeError):
    """页面上下文 API 请求失败。"""


class PageApiBusinessError(PageApiError):
    """页面请求成功到达服务端，但服务端返回了业务错误码。"""

    def __init__(self, endpoint: str, business_code: Any, message: str) -> None:
        self.endpoint = endpoint
        self.business_code = business_code
        self.business_message = message
        super().__init__(
            f"{endpoint} 业务失败 code={business_code} message={message[:160]}"
        )


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
    page_query: dict[str, str] = field(default_factory=dict)


def get_affiliate_page_context(
    store_id: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> AffiliatePageContext:
    """读取当前页面 URL 中 API 所需的店铺上下文，不读取会话信息。"""
    raise_if_cancelled(cancel_check)
    result = zclaw_exec(
        store_id,
        "(() => JSON.stringify({href: location.href || ''}))()",
        timeout=HREF_PROBE_TIMEOUT_SECONDS,
        retries=2,
        retry_base_sec=0.5,
        retry_timeout_expired=True,
        operation="affiliate_context_read",
    )
    raise_if_cancelled(cancel_check)
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


_SELLER_CONTEXT_JS = r"""
(() => {
  const nav = navigator || {};
  const scr = typeof screen === 'undefined' ? {} : screen;
  let timezoneName = '';
  try {
    timezoneName = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch (error) {
    timezoneName = '';
  }
  let fp = '';
  try {
    const candidates = [window.fp, window.__fp];
    for (const value of candidates) {
      if (typeof value === 'string' && value.trim()) {
        fp = value.trim();
        break;
      }
    }
  } catch (error) {
    fp = '';
  }
  const pageLang = (document.documentElement && document.documentElement.lang) || '';
  return JSON.stringify({
    href: location.href || '',
    locale: pageLang || 'zh-CN',
    language: pageLang || nav.language || 'zh-CN',
    browser_language: nav.language || pageLang || 'zh-CN',
    browser_name: 'Mozilla',
    browser_online: String(nav.onLine !== false),
    browser_platform: nav.platform || '',
    browser_version: String(nav.userAgent || '').slice(0, 240),
    cookie_enabled: String(!!nav.cookieEnabled),
    device_platform: 'web',
    screen_width: String(scr.width || ''),
    screen_height: String(scr.height || ''),
    timezone_name: timezoneName,
    fp: fp
  });
})()
"""

_SELLER_PAGE_QUERY_KEYS = (
    "locale",
    "language",
    "browser_language",
    "browser_name",
    "browser_online",
    "browser_platform",
    "browser_version",
    "cookie_enabled",
    "device_platform",
    "screen_width",
    "screen_height",
    "timezone_name",
    "fp",
)


def _seller_page_query_from_probe(probe: dict[str, Any]) -> dict[str, str]:
    page_query: dict[str, str] = {}
    for key in _SELLER_PAGE_QUERY_KEYS:
        text = str(probe.get(key) or "").strip()
        if text:
            page_query[key] = text
    return page_query


def get_seller_page_context(
    store_id: str,
    *,
    shop_id: str,
    shop_region: str = "US",
) -> SellerPageContext:
    """读取当前商家订单页上下文；店铺 ID 来自已读样品列表行。"""
    result = zclaw_exec(
        store_id,
        _SELLER_CONTEXT_JS,
        timeout=HREF_PROBE_TIMEOUT_SECONDS,
        retries=2,
        retry_base_sec=0.5,
        retry_timeout_expired=True,
        operation="seller_context_read",
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
        page_query=_seller_page_query_from_probe(result),
    )


def build_seller_request_query(
    context: SellerPageContext,
    extra_query: dict[str, Any] | None = None,
) -> dict[str, str]:
    """订单页物流 GET 基参：对齐前端，不用 shop_id/shop_region。"""
    shop_id = str(context.shop_id or "").strip()
    query: dict[str, str] = {
        "aid": SELLER_AID,
        "app_name": SELLER_APP_NAME,
        "oec_seller_id": shop_id,
        "seller_id": shop_id,
    }
    for key, value in (context.page_query or {}).items():
        text = str(value or "").strip()
        if text:
            query[str(key)] = text
    if extra_query:
        for key, value in extra_query.items():
            if value is None:
                continue
            text = str(value).strip()
            if text:
                query[str(key)] = text
    return query


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
        query = build_seller_request_query(context, extra_query)
        extra_query = None
    if extra_query:
        query.update({str(key): str(value) for key, value in extra_query.items()})
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
    deadline: float | None = None,
    retry_timeout_expired: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """在页面上下文执行一个已登记的异步 POST。

    ``deadline`` 存在时，poll subprocess 超时、轮询 sleep 和 retry backoff
    都裁剪到剩余预算内；浏览器端 AbortController 保留，但 Python 外层
    不得超过总 deadline。
    """
    request_url = _build_request_url(
        endpoint,
        context=context,
        allowed_endpoints=allowed_endpoints,
        extra_query=request_query,
    )
    attempts = max(1, int(retries) + 1)
    resolved_deadline = (
        deadline
        if deadline is not None
        else deadline_from_timeout(
            request_timeout_seconds * attempts
            + max(0.1, poll_interval_seconds) * 20 * attempts
            + 0.8 * attempts * (attempts + 1)
            + 10.0
        )
    )
    last_error: PageApiError | None = None
    for attempt in range(attempts):
        raise_if_cancelled(cancel_check)
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

  const requestHeaders = {{
    'accept': 'application/json, text/plain, */*'
  }};
  if (requestMethod !== 'GET' && requestMethod !== 'HEAD') {{
    requestHeaders['content-type'] = 'application/json';
  }}
  const requestOptions = {{
    method: requestMethod,
    headers: requestHeaders,
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
            raise_if_cancelled(cancel_check)
            start_result = zclaw_exec(
                store_id,
                start_script,
                timeout=30,
                retries=start_exec_retries,
                retry_base_sec=0.8,
                retry_timeout_expired=retry_timeout_expired,
                deadline=resolved_deadline,
                operation=f"page_api_start_{http_method.lower()}",
            )
            if not isinstance(start_result, dict) or not start_result.get("started"):
                raise PageApiError(
                    f"{endpoint} 无法启动页面异步请求: {start_result!r}"[:400]
                )

            raise_if_cancelled(cancel_check)
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
            poll_deadline = min(
                resolved_deadline,
                time.monotonic() + max(1.0, request_timeout_seconds + 5.0),
            )
            result: dict[str, Any] | None = None
            while remaining_seconds(poll_deadline) > 0:
                raise_if_cancelled(cancel_check)
                if not sleep_until_next_probe(
                    poll_deadline, max(0.1, poll_interval_seconds)
                ):
                    break
                raise_if_cancelled(cancel_check)
                poll_timeout = max(
                    1,
                    min(30, int(remaining_seconds(poll_deadline))),
                )
                poll_result = zclaw_exec(
                    store_id,
                    poll_script,
                    timeout=poll_timeout,
                    retries=poll_exec_retries,
                    retry_base_sec=0.8,
                    retry_timeout_expired=retry_timeout_expired,
                    deadline=poll_deadline,
                    operation=f"page_api_poll_{http_method.lower()}",
                )
                if not isinstance(poll_result, dict):
                    raise PageApiError(
                        f"{endpoint} 轮询返回未知类型: {type(poll_result).__name__}"
                    )
                raise_if_cancelled(cancel_check)
                if poll_result.get("done"):
                    result = poll_result
                    break
            if result is None:
                if remaining_seconds(resolved_deadline) <= 0:
                    raise LogisticsRequestTimeout(
                        f"{endpoint} 页面异步请求耗尽总 deadline"
                    )
                raise PageApiError(f"{endpoint} 页面异步请求轮询超时")
        except OperationCancelled:
            raise
        except Exception as error:
            last_error = (
                error
                if isinstance(error, (PageApiError, LogisticsRequestTimeout))
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
                    raise PageApiBusinessError(
                        endpoint,
                        business_code,
                        str(payload.get("message") or ""),
                    )
                verbose_print(
                    f"[页面API] {operation_label} {http_method.upper()} {endpoint} "
                    f"status={result.get('status')} code={business_code} "
                    f"elapsed_ms={elapsed_ms}",
                    flush=True,
                )
                return payload

        if attempt + 1 < attempts:
            raise_if_cancelled(cancel_check)
            if not sleep_until_next_probe(resolved_deadline, 0.8 * (attempt + 1)):
                last_error = LogisticsRequestTimeout(
                    f"{endpoint} 重试退避耗尽总 deadline"
                )
                break

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
    cancel_check: Callable[[], bool] | None = None,
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
        cancel_check=cancel_check,
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


def get_affiliate_read_json(
    store_id: str,
    endpoint: str,
    *,
    query: dict[str, Any],
    context: AffiliatePageContext,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    retries: int = 2,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    deadline: float | None = None,
) -> dict[str, Any]:
    """在联盟中心页面执行已登记的只读 GET。"""
    return _post_page_json(
        store_id,
        endpoint,
        {},
        context=context,
        allowed_endpoints=AFFILIATE_READ_GET_ENDPOINTS,
        operation_label="联盟中心只读",
        max_response_bytes=max_response_bytes,
        retries=retries,
        request_timeout_seconds=request_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        start_exec_retries=1,
        poll_exec_retries=1,
        http_method="GET",
        request_query=query,
        deadline=deadline,
        retry_timeout_expired=True,
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
    deadline: float | None = None,
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
        deadline=deadline,
        retry_timeout_expired=True,
    )
