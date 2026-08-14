#!/usr/bin/env python3
"""从 TikTok Shop 商家中心首页导航到样品申请待审核。"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from .sample_dom import assert_on_pending_list
from .zclaw import zclaw_exec

SAMPLE_REQUEST_URL = (
    "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request"
    "?shop_region=US"
)

INSPECT_NAVIGATION_PAGE_JS = r"""
(() => {
  const href = location.href || '';
  const hostname = location.hostname || '';
  const pathname = location.pathname || '';
  const title = document.title || '';
  const bodyText = (document.body && document.body.innerText || '').slice(0, 12000);
  const samplePage = hostname === 'affiliate.tiktokshopglobalselling.com'
    && /sample-request/i.test(pathname);
  const affiliatePage = hostname === 'affiliate.tiktokshopglobalselling.com';
  const sellerHost = /^seller(?:\.[a-z0-9-]+)*\.tiktokshopglobalselling\.com$/i.test(hostname)
    || /^seller(?:\.[a-z0-9-]+)*\.tiktokglobalshop\.com$/i.test(hostname)
    || /^seller(?:\.[a-z0-9-]+)*\.tiktok\.com$/i.test(hostname);
  const loginPage = /login|sign[-_]?in|passport/i.test(pathname + ' ' + title)
    || /Log in to TikTok Shop|登录 TikTok Shop|Sign in to TikTok Shop/i.test(bodyText);
  const sellerCenterEvidence = /Seller Center|Shop Seller Center|商家中心|TikTok Shop/i.test(
    title + '\n' + bodyText
  );
  return JSON.stringify({
    ok: true,
    href,
    hostname,
    pathname,
    title: title.slice(0, 160),
    ready_state: document.readyState,
    page_type: samplePage
      ? 'sample-request'
      : (loginPage
          ? 'login'
          : (sellerHost && sellerCenterEvidence
              ? 'seller-center'
              : (affiliatePage ? 'affiliate-center' : 'unknown'))),
  });
})()
"""

INSPECT_PENDING_LIST_READINESS_JS = r"""
(() => {
  const bodyText = document.body && document.body.innerText || '';
  const rowCount = document.querySelectorAll('table tbody tr').length;
  const hasEmptyState = /暂无数据|No data|No applications|没有申请/i.test(bodyText);
  return JSON.stringify({
    ok: true,
    href: location.href || '',
    ready_state: document.readyState,
    row_count: rowCount,
    has_empty_state: hasEmptyState,
    ready: document.readyState === 'complete' && (rowCount > 0 || hasEmptyState),
  });
})()
"""


def validate_navigation_start(page_state: dict[str, Any]) -> str:
    """校验自动导航起点；返回可接受的页面类型。"""
    href = str(page_state.get("href") or "").strip()
    page_type = str(page_state.get("page_type") or "").strip()
    if not href or href == "about:blank":
        raise RuntimeError("店铺页面仍是 about:blank；请先登录并停在商家中心首页")
    if page_type == "login":
        raise RuntimeError("检测到 TikTok Shop 登录页；请先完成登录")
    if page_type not in {"seller-center", "affiliate-center", "sample-request"}:
        raise RuntimeError(
            "当前页无法确认是已登录的 TikTok Shop 商家中心或联盟中心页面。"
            f" href={href[:180]} title={str(page_state.get('title') or '')[:100]}"
        )
    return page_type


def validate_sample_request_destination(page_state: dict[str, Any]) -> dict[str, str]:
    """确认导航结果属于联盟样品申请页，并且已有店铺上下文。"""
    href = str(page_state.get("href") or "").strip()
    parsed_url = urllib.parse.urlsplit(href)
    query = urllib.parse.parse_qs(parsed_url.query)
    shop_id = str((query.get("shop_id") or [""])[0]).strip()
    shop_region = str((query.get("shop_region") or [""])[0]).strip().upper()
    if parsed_url.hostname != "affiliate.tiktokshopglobalselling.com":
        raise RuntimeError(f"导航后未进入 TikTok Shop 联盟中心: {href[:180]}")
    if "sample-request" not in parsed_url.path:
        raise RuntimeError(f"导航后未进入样品申请页: {href[:180]}")
    if not shop_id:
        raise RuntimeError(f"样品申请页尚未带 shop_id 店铺上下文: {href[:180]}")
    if not shop_region:
        raise RuntimeError(f"样品申请页尚未带 shop_region: {href[:180]}")
    return {
        "href": href,
        "shop_id": shop_id,
        "shop_region": shop_region,
    }


def sample_request_url(*, shop_id: str = "", shop_region: str = "US") -> str:
    """带上已知店铺上下文，避免回样品申请页时落到无 shop_id 的中间态。"""
    query = {
        "shop_region": str(shop_region or "US").strip().upper() or "US",
    }
    normalized_shop_id = str(shop_id or "").strip()
    if normalized_shop_id:
        query["shop_id"] = normalized_shop_id
    return (
        "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request"
        f"?{urllib.parse.urlencode(query)}"
    )


def current_page_href(
    store_id: str,
    *,
    execute_script_fn: Callable[..., Any] = zclaw_exec,
) -> str:
    result = execute_script_fn(
        store_id,
        "(() => JSON.stringify({href: location.href || ''}))()",
        timeout=15,
        retries=0,
    )
    if isinstance(result, dict):
        return str(result.get("href") or "").strip()
    return ""


def is_sample_request_href(href: str) -> bool:
    parsed_url = urllib.parse.urlsplit(href or "")
    return (
        parsed_url.hostname == "affiliate.tiktokshopglobalselling.com"
        and "sample-request" in parsed_url.path
    )


def is_seller_order_href(href: str) -> bool:
    parsed_url = urllib.parse.urlsplit(href or "")
    hostname = str(parsed_url.hostname or "").lower()
    return bool(
        re.fullmatch(
            r"seller(?:\.[a-z0-9-]+)*\.tiktokshopglobalselling\.com",
            hostname,
        )
        and "/order" in parsed_url.path
    )


def navigate_to_url(
    store_id: str,
    url: str,
    href_matches: Callable[[str], bool],
    *,
    timeout: float = 20.0,
    poll_interval: float = 0.5,
    execute_script_fn: Callable[..., Any] = zclaw_exec,
    navigate_page_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """当前已在目标页则跳过；否则异步 assign 后短轮询 href。"""
    current_href = ""
    try:
        current_href = current_page_href(
            store_id,
            execute_script_fn=execute_script_fn,
        )
    except Exception:
        current_href = ""
    if href_matches(current_href):
        return {"ok": True, "href": current_href, "already": True, "target_url": url}

    navigator = navigate_page_fn or schedule_page_navigation
    navigator(
        store_id,
        url,
        execute_script_fn=execute_script_fn,
    )
    arrived_href = wait_for_page_href(
        store_id,
        href_matches,
        timeout=timeout,
        poll_interval=poll_interval,
        execute_script_fn=execute_script_fn,
    )
    return {
        "ok": True,
        "href": arrived_href,
        "already": False,
        "target_url": url,
    }


def wait_for_page_href(
    store_id: str,
    href_matches: Callable[[str], bool],
    *,
    timeout: float = 20.0,
    poll_interval: float = 0.5,
    execute_script_fn: Callable[..., Any] = zclaw_exec,
) -> str:
    """短轮询当前 href，直到命中目标页。不使用阻塞 visit_page。"""
    deadline = time.monotonic() + max(1.0, timeout)
    last_href = ""
    last_error = ""
    while time.monotonic() < deadline:
        try:
            last_href = current_page_href(
                store_id,
                execute_script_fn=execute_script_fn,
            )
            if href_matches(last_href):
                return last_href
        except Exception as error:
            last_error = str(error)
        time.sleep(max(0.2, poll_interval))
    raise RuntimeError(
        "页面跳转超时。"
        f" last_href={last_href[:180]}"
        f" error={last_error}"
    )


def navigate_to_sample_request(
    store_id: str,
    *,
    shop_id: str = "",
    shop_region: str = "US",
    timeout: float = 20.0,
    poll_interval: float = 0.5,
    execute_script_fn: Callable[..., Any] = zclaw_exec,
    navigate_page_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """已在样品申请页则直接返回；否则异步跳转并短轮询，避免 visit_page 假死。"""
    return navigate_to_url(
        store_id,
        sample_request_url(shop_id=shop_id, shop_region=shop_region),
        is_sample_request_href,
        timeout=timeout,
        poll_interval=poll_interval,
        execute_script_fn=execute_script_fn,
        navigate_page_fn=navigate_page_fn,
    )


def schedule_page_navigation(
    store_id: str,
    url: str,
    *,
    execute_script_fn: Callable[..., Any] = zclaw_exec,
) -> dict[str, Any]:
    """先返回执行结果，再异步导航，避免页面销毁导致 Bridge 假 network。"""
    navigation_script = f"""
(() => {{
  const targetUrl = {json.dumps(url, ensure_ascii=False)};
  window.setTimeout(() => window.location.assign(targetUrl), 100);
  return JSON.stringify({{ok: true, scheduled: true, target_url: targetUrl}});
}})()
"""
    result = execute_script_fn(
        store_id,
        navigation_script,
        timeout=30,
        retries=1,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"安排页面导航失败: {result!r}"[:300])
    return result


def navigate_from_seller_home_to_pending(
    store_id: str,
    *,
    navigation_timeout: float = 45.0,
    poll_interval: float = 1.5,
    navigate_page_fn: Callable[[str, str], dict[str, Any]] = schedule_page_navigation,
    execute_script_fn: Callable[..., Any] = zclaw_exec,
    ensure_pending_fn: Callable[..., dict[str, Any]] = assert_on_pending_list,
) -> dict[str, Any]:
    """显式开启时，从商家中心首页导航并切到待审核 tab。"""
    initial_state = execute_script_fn(
        store_id,
        INSPECT_NAVIGATION_PAGE_JS,
        timeout=30,
        retries=1,
    )
    if not isinstance(initial_state, dict):
        raise RuntimeError(f"无法识别当前店铺页面: {initial_state!r}"[:300])
    initial_page_type = validate_navigation_start(initial_state)

    # 显式导航模式始终访问规范 URL，避免上次扫表停在中间分页后从该页续扫。
    navigate_page_fn(store_id, SAMPLE_REQUEST_URL)
    time.sleep(max(2.0, poll_interval))

    deadline = time.monotonic() + max(1.0, navigation_timeout)
    destination_state: dict[str, Any] = {}
    destination_context: dict[str, str] | None = None
    last_validation_error = ""
    while time.monotonic() < deadline:
        try:
            candidate_state = execute_script_fn(
                store_id,
                INSPECT_NAVIGATION_PAGE_JS,
                timeout=15,
                retries=0,
            )
        except Exception as error:
            candidate_state = None
            last_validation_error = str(error)
        if isinstance(candidate_state, dict):
            destination_state = candidate_state
            try:
                destination_context = validate_sample_request_destination(candidate_state)
                break
            except RuntimeError as error:
                last_validation_error = str(error)
        time.sleep(max(0.2, poll_interval))

    if destination_context is None:
        raise RuntimeError(
            "自动导航样品申请页超时。"
            f" 最后页面={str(destination_state.get('href') or '')[:180]}"
            f" 校验={last_validation_error}"
        )

    pending_result = ensure_pending_fn(
        store_id,
        page_wait=max(0.5, poll_interval),
        retries=4,
    )
    readiness_deadline = time.monotonic() + max(5.0, navigation_timeout)
    readiness_state: dict[str, Any] = {}
    while time.monotonic() < readiness_deadline:
        try:
            candidate_readiness = execute_script_fn(
                store_id,
                INSPECT_PENDING_LIST_READINESS_JS,
                timeout=15,
                retries=0,
            )
        except Exception:
            candidate_readiness = None
        if isinstance(candidate_readiness, dict):
            readiness_state = candidate_readiness
            if candidate_readiness.get("ready"):
                break
        time.sleep(max(0.2, poll_interval))
    else:
        raise RuntimeError(
            "已进入待审核，但列表数据在超时前未就绪。"
            f" href={str(readiness_state.get('href') or '')[:180]}"
            f" rows={readiness_state.get('row_count')}"
        )

    return {
        "ok": True,
        "initial_page_type": initial_page_type,
        "destination": destination_context,
        "pending_tab": pending_result,
        "list_readiness": readiness_state,
    }
