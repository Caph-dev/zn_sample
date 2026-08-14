#!/usr/bin/env python3
"""紫鸟页面上下文中的 TikTok 只读 API 传输层。

请求始终由当前紫鸟店铺页面发出，保留该页面的登录态、网络出口和浏览器环境。
本模块不读取、不导出 Cookie，也不提供任意写接口。
"""
from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .zclaw import DEFAULT_EXEC_RETRIES, zclaw_exec

AFFILIATE_HOST = "affiliate.tiktokshopglobalselling.com"
SAMPLE_LIST_ENDPOINT = "/api/v1/affiliate/sample/group/list"
CREATOR_PROFILE_ENDPOINT = "/api/v1/oec/affiliate/creator/marketplace/profile"
READ_POST_ENDPOINTS = frozenset(
    {
        SAMPLE_LIST_ENDPOINT,
        CREATOR_PROFILE_ENDPOINT,
    }
)
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class PageApiError(RuntimeError):
    """页面上下文 API 请求失败。"""


class PageApiSchemaError(PageApiError):
    """API 响应结构不符合已验证契约。"""


@dataclass(frozen=True)
class AffiliatePageContext:
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


def _build_read_url(
    endpoint: str,
    *,
    context: AffiliatePageContext,
    extra_query: dict[str, str] | None = None,
) -> str:
    if endpoint not in READ_POST_ENDPOINTS:
        raise PageApiError(f"未登记的只读 POST endpoint: {endpoint}")
    query: dict[str, str] = {
        "user_language": "zh-CN",
        "aid": "6556",
        "app_name": "i18n_ecom_alliance",
        "device_id": "0",
        "device_platform": "web",
        "oec_seller_id": context.shop_id,
        "shop_region": context.shop_region,
    }
    query.update(extra_query or {})
    return f"{endpoint}?{urllib.parse.urlencode(query)}"


def post_read_json(
    store_id: str,
    endpoint: str,
    body: dict[str, Any],
    *,
    context: AffiliatePageContext,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    retries: int = 2,
) -> dict[str, Any]:
    """在当前页面中执行已登记的查询型 POST，并返回业务 payload。"""
    request_url = _build_read_url(endpoint, context=context)
    script = f"""
(() => {{
  const requestUrl = {json.dumps(request_url, ensure_ascii=False)};
  const requestBody = {json.dumps(body, ensure_ascii=False)};
  const maxResponseBytes = {int(max_response_bytes)};
  const request = new XMLHttpRequest();
  try {{
    request.open('POST', requestUrl, false);
    request.setRequestHeader('accept', 'application/json, text/plain, */*');
    request.setRequestHeader('content-type', 'application/json');
    request.send(JSON.stringify(requestBody));
    const responseText = request.responseText || '';
    const responseByteLength = new TextEncoder().encode(responseText).length;
    if (responseByteLength > maxResponseBytes) {{
      return JSON.stringify({{
        ok: false,
        error_type: 'response-too-large',
        status: request.status,
        response_length: responseByteLength
      }});
    }}
    let payload = null;
    try {{
      payload = JSON.parse(responseText);
    }} catch (error) {{
      return JSON.stringify({{
        ok: false,
        error_type: 'invalid-json',
        status: request.status,
        response_length: responseByteLength
      }});
    }}
    return JSON.stringify({{
      ok: request.status >= 200 && request.status < 300,
      status: request.status,
      payload
    }});
  }} catch (error) {{
    return JSON.stringify({{
      ok: false,
      error_type: 'request-exception',
      error: String(error)
    }});
  }}
}})()
"""

    attempts = max(1, int(retries) + 1)
    last_error: PageApiError | None = None
    for attempt in range(attempts):
        started_at = time.monotonic()
        try:
            result = zclaw_exec(
                store_id,
                script,
                timeout=90,
                retries=DEFAULT_EXEC_RETRIES,
            )
        except Exception as error:
            last_error = PageApiError(f"{endpoint} Bridge 请求失败: {error}")
        else:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            if not isinstance(result, dict):
                last_error = PageApiError(
                    f"{endpoint} 返回未知封装类型: {type(result).__name__}"
                )
            elif not result.get("ok"):
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
                print(
                    f"[页面API] POST {endpoint} status={result.get('status')} "
                    f"code={business_code} elapsed_ms={elapsed_ms}",
                    flush=True,
                )
                return payload

        if attempt + 1 < attempts:
            time.sleep(0.8 * (attempt + 1))

    assert last_error is not None
    raise last_error
