#!/usr/bin/env python3
"""从已登录的 TikTok Shop 商家中心跳转到样品申请待审核。"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from .debug_log import debug_log
from .sample_dom import assert_on_pending_list
from .sync_errors import SellerNavigationTimeout, SellerPageReadinessTimeout
from .time_budget import (
    deadline_from_timeout,
    remaining_seconds,
    sleep_until_next_probe,
)
from .zclaw import (
    HREF_PROBE_TIMEOUT_SECONDS,
    is_timeout_expired_error,
    zclaw_exec,
)

SAMPLE_REQUEST_URL = (
    "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request"
    "?shop_region=US"
)
# 订单页 SPA 会先闪到样品申请再弹回；连续两次同一 href 才算站住。
STABLE_DESTINATION_POLLS = 2

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
          : (sellerHost
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

INSPECT_SELLER_ORDER_READINESS_JS = r"""
(() => {
  const href = location.href || '';
  const hostname = location.hostname || '';
  const pathname = location.pathname || '';
  const title = document.title || '';
  const bodyText = (document.body && document.body.innerText || '').slice(0, 12000);
  const loginPage = /login|sign[-_]?in|passport/i.test(pathname + ' ' + title)
    || /Log in to TikTok Shop|登录 TikTok Shop|Sign in to TikTok Shop/i.test(bodyText);
  const errorPage = /error\.html/i.test(pathname);
  const appRoot = !!document.querySelector(
    '#layout, #root, #app, #__next, [id^="app"], [data-app]'
  );
  return JSON.stringify({
    ok: true,
    href,
    ready_state: document.readyState,
    has_document_element: !!document.documentElement,
    has_body: !!document.body,
    has_app_root: appRoot,
    is_login_page: loginPage,
    is_error_page: errorPage,
  });
})()
"""

SELLER_HOST_PATTERN = re.compile(
    r"^seller(?:\.[a-z0-9-]+)*\.tiktokshopglobalselling\.com$",
    re.IGNORECASE,
)


def validate_navigation_start(page_state: dict[str, Any]) -> str:
    """校验自动导航起点；返回可接受的页面类型。"""
    href = str(page_state.get("href") or "").strip()
    page_type = str(page_state.get("page_type") or "").strip()
    if not href or href == "about:blank":
        raise RuntimeError("店铺页面仍是 about:blank；请先登录商家中心")
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
    timeout: float = HREF_PROBE_TIMEOUT_SECONDS,
    deadline: float | None = None,
) -> str:
    """读取当前 href；只读探测，TimeoutExpired 允许有限退避。"""
    result = execute_script_fn(
        store_id,
        "(() => JSON.stringify({href: location.href || ''}))()",
        timeout=max(1, int(timeout)),
        retries=2,
        retry_base_sec=0.5,
        retry_timeout_expired=True,
        deadline=deadline,
        operation="href_probe",
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


def is_ziniao_navigation_error_href(href: str) -> bool:
    """紫鸟拦截页：跳转失败，不能再当订单页轮询。"""
    parsed_url = urllib.parse.urlsplit(href or "")
    path = str(parsed_url.path or "").lower()
    return parsed_url.scheme == "chrome-extension" and "error.html" in path


def validate_seller_order_readiness(state: dict[str, Any]) -> str:
    """校验订单页稳定契约；全部满足才返回 canonical href。

    不要求具体订单出现在表格或全部异步组件加载完成，
    只确认页面 runtime 稳定、seller 同源上下文可用。
    """
    href = str(state.get("href") or "").strip()
    parsed_url = urllib.parse.urlsplit(href)
    hostname = str(parsed_url.hostname or "").lower()
    if not SELLER_HOST_PATTERN.fullmatch(hostname):
        raise RuntimeError(f"订单页未命中已验证 seller host: {href[:180]}")
    if "/order" not in str(parsed_url.path or ""):
        raise RuntimeError(f"订单页路径不含 /order: {href[:180]}")
    if state.get("is_error_page"):
        raise RuntimeError(f"订单页停在紫鸟 error.html: {href[:180]}")
    if state.get("is_login_page"):
        raise RuntimeError(f"订单页跳到了登录页: {href[:180]}")
    ready_state = str(state.get("ready_state") or "")
    if ready_state not in {"interactive", "complete"}:
        raise RuntimeError(
            f"订单页 readyState={ready_state or 'empty'} 尚未就绪: {href[:180]}"
        )
    if not state.get("has_document_element"):
        raise RuntimeError(f"订单页缺少 documentElement: {href[:180]}")
    if not state.get("has_body"):
        raise RuntimeError(f"订单页缺少 document.body: {href[:180]}")
    if not state.get("has_app_root"):
        raise RuntimeError(f"订单页找不到商家中心应用根节点: {href[:180]}")
    return href


def wait_for_seller_order_page_ready(
    store_id: str,
    *,
    deadline: float,
    required_stable_probes: int = 2,
    poll_interval: float = 0.5,
    execute_script_fn: Callable[..., Any] = zclaw_exec,
) -> dict[str, Any]:
    """轮询订单页稳定契约，连续 N 次成功才认为稳定。稳定前禁止物流 GET。"""
    stable_href = ""
    stable_count = 0
    last_state: dict[str, Any] = {}
    last_validation_error = ""
    while remaining_seconds(deadline) > 0:
        probe_timeout = min(HREF_PROBE_TIMEOUT_SECONDS, remaining_seconds(deadline))
        if probe_timeout < 0.5:
            # 剩余时间不足以完成最小 probe，不再启动 subprocess。
            break
        try:
            state = execute_script_fn(
                store_id,
                INSPECT_SELLER_ORDER_READINESS_JS,
                timeout=max(1, int(probe_timeout)),
                retries=2,
                retry_base_sec=0.5,
                retry_timeout_expired=True,
                deadline=deadline,
                operation="seller_order_readiness_probe",
            )
        except Exception as error:
            last_validation_error = str(error)
            stable_href = ""
            stable_count = 0
            if not sleep_until_next_probe(deadline, poll_interval):
                break
            continue
        if not isinstance(state, dict):
            last_validation_error = f"订单页探测返回未知类型: {type(state).__name__}"
            stable_href = ""
            stable_count = 0
            if not sleep_until_next_probe(deadline, poll_interval):
                break
            continue
        last_state = state
        try:
            href = validate_seller_order_readiness(state)
        except RuntimeError as error:
            last_validation_error = str(error)
            stable_href = ""
            stable_count = 0
            if not sleep_until_next_probe(deadline, poll_interval):
                break
            continue
        if href == stable_href:
            stable_count += 1
        else:
            stable_href = href
            stable_count = 1
        if stable_count >= max(1, int(required_stable_probes)):
            return state
        # 两次成功探测之间保留 300–500ms 的短间隔。
        if not sleep_until_next_probe(deadline, min(max(0.3, poll_interval), 0.5)):
            break
    raise SellerPageReadinessTimeout(
        "订单页稳定就绪超时。"
        f" last_href={str(last_state.get('href') or '')[:180]}"
        f" ready={last_state.get('ready_state')}"
        f" stable_count={stable_count}"
        f" 校验={last_validation_error}"
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
    deadline: float | None = None,
) -> dict[str, Any]:
    """当前已在目标页则跳过；否则异步 replace 后短轮询 href。

    所有探测、settle 等待和轮询都服从真实墙钟 deadline；
    ``TimeoutExpired`` 消耗的真实时间同样计入预算。
    """
    resolved_deadline = (
        deadline if deadline is not None else deadline_from_timeout(timeout)
    )
    current_href = ""
    try:
        probe_timeout = min(
            HREF_PROBE_TIMEOUT_SECONDS,
            remaining_seconds(resolved_deadline),
        )
        if probe_timeout >= 1.0:
            current_href = current_page_href(
                store_id,
                execute_script_fn=execute_script_fn,
                timeout=probe_timeout,
                deadline=resolved_deadline,
            )
    except Exception:
        current_href = ""
    if href_matches(current_href):
        # region agent log
        debug_log(
            "navigate-already",
            location="scripts/lib/sample_navigation.py:navigate_to_url",
            hypothesisId="H-nav",
            from_href=current_href[:180],
            target_url=url[:180],
        )
        # endregion
        return {"ok": True, "href": current_href, "already": True, "target_url": url}

    navigator = navigate_page_fn or schedule_page_navigation
    # region agent log
    debug_log(
        "navigate-start",
        location="scripts/lib/sample_navigation.py:navigate_to_url",
        hypothesisId="H-nav",
        from_href=current_href[:180],
        target_url=url[:180],
        timeout=timeout,
    )
    # endregion
    try:
        navigator(
            store_id,
            url,
            execute_script_fn=execute_script_fn,
        )
    except Exception as error:
        # 导航调度 TimeoutExpired 不能盲目重放：第一次调用可能已经安排了
        # location.replace。先短 probe 当前页，已到目标页则视为成功。
        if not is_timeout_expired_error(error):
            raise
        probed_href = ""
        try:
            probe_timeout = min(
                HREF_PROBE_TIMEOUT_SECONDS,
                remaining_seconds(resolved_deadline),
            )
            if probe_timeout >= 0.5:
                probed_href = current_page_href(
                    store_id,
                    execute_script_fn=execute_script_fn,
                    timeout=probe_timeout,
                    deadline=resolved_deadline,
                )
        except Exception:
            probed_href = ""
        if href_matches(probed_href):
            # region agent log
            debug_log(
                "navigate-schedule-timeout-but-arrived",
                location="scripts/lib/sample_navigation.py:navigate_to_url",
                hypothesisId="H-nav",
                arrived_href=probed_href[:180],
                target_url=url[:180],
            )
            # endregion
            return {"ok": True, "href": probed_href, "already": False, "target_url": url}
        if remaining_seconds(resolved_deadline) >= 1.0:
            navigator(
                store_id,
                url,
                execute_script_fn=execute_script_fn,
            )
        else:
            raise
    # 跨域跳转会卸页；立刻 execute_script 常卡死并把整段等待吃掉。
    # 样品申请 → seller.us 实测约 2.5s 后 href 才变成订单页，先硬等再轮询。
    settle_seconds = max(2.0, min(3.5, poll_interval * 4)) if timeout >= 5 else max(0.2, poll_interval)
    if not sleep_until_next_probe(resolved_deadline, settle_seconds):
        raise SellerNavigationTimeout(
            f"页面导航 settle 等待耗尽 deadline: target={url[:180]}"
        )
    try:
        arrived_href = wait_for_page_href(
            store_id,
            href_matches,
            deadline=resolved_deadline,
            poll_interval=poll_interval,
            execute_script_fn=execute_script_fn,
        )
    except Exception as error:
        # region agent log
        debug_log(
            "navigate-fail",
            location="scripts/lib/sample_navigation.py:navigate_to_url",
            hypothesisId="H-nav",
            from_href=current_href[:180],
            target_url=url[:180],
            error_type=type(error).__name__,
            error=str(error)[:400],
        )
        # endregion
        raise
    # region agent log
    debug_log(
        "navigate-ok",
        location="scripts/lib/sample_navigation.py:navigate_to_url",
        hypothesisId="H-nav",
        from_href=current_href[:180],
        arrived_href=arrived_href[:180],
        target_url=url[:180],
    )
    # endregion
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
    deadline: float | None = None,
) -> str:
    """短轮询当前 href，直到命中目标页。不使用阻塞 visit_page。

    每个 probe（包括其 ``TimeoutExpired``）都消耗真实墙钟预算；
    剩余时间不足以完成最小 probe 时，不再启动新的 subprocess。
    """
    resolved_deadline = (
        deadline if deadline is not None else deadline_from_timeout(timeout)
    )
    last_href = ""
    last_error = ""
    while True:
        remaining = remaining_seconds(resolved_deadline)
        if remaining <= 0:
            break
        probe_timeout = min(HREF_PROBE_TIMEOUT_SECONDS, remaining)
        if probe_timeout < 0.5:
            break
        try:
            last_href = current_page_href(
                store_id,
                execute_script_fn=execute_script_fn,
                timeout=probe_timeout,
                deadline=resolved_deadline,
            )
        except Exception as error:
            last_error = str(error)
            last_href = ""
            if not sleep_until_next_probe(resolved_deadline, max(0.2, poll_interval)):
                break
            continue
        if is_ziniao_navigation_error_href(last_href):
            raise RuntimeError(
                "订单页跳转被紫鸟拦截，停在 error.html；"
                f" last_href={last_href[:180]}"
            )
        if href_matches(last_href):
            return last_href
        if not sleep_until_next_probe(resolved_deadline, max(0.2, poll_interval)):
            break
    raise SellerNavigationTimeout(
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
  window.setTimeout(() => {{
    const target = window.top || window;
    try {{
      target.location.replace(targetUrl);
    }} catch (error) {{
      window.location.replace(targetUrl);
    }}
  }}, 100);
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


def _wait_for_sample_request_destination(
    store_id: str,
    *,
    navigation_timeout: float,
    poll_interval: float,
    execute_script_fn: Callable[..., Any],
) -> dict[str, str]:
    deadline = deadline_from_timeout(max(1.0, navigation_timeout))
    destination_state: dict[str, Any] = {}
    last_validation_error = ""
    stable_href = ""
    stable_count = 0
    while remaining_seconds(deadline) > 0:
        try:
            candidate_state = execute_script_fn(
                store_id,
                INSPECT_NAVIGATION_PAGE_JS,
                timeout=HREF_PROBE_TIMEOUT_SECONDS,
                retries=0,
            )
        except Exception as error:
            candidate_state = None
            last_validation_error = str(error)
            stable_href = ""
            stable_count = 0
        if isinstance(candidate_state, dict):
            destination_state = candidate_state
            try:
                destination_context = validate_sample_request_destination(candidate_state)
            except RuntimeError as error:
                last_validation_error = str(error)
                stable_href = ""
                stable_count = 0
            else:
                href = destination_context["href"]
                if href == stable_href:
                    stable_count += 1
                else:
                    stable_href = href
                    stable_count = 1
                if stable_count >= STABLE_DESTINATION_POLLS:
                    return destination_context
        if not sleep_until_next_probe(deadline, max(0.2, poll_interval)):
            break
    raise RuntimeError(
        "自动导航样品申请页超时。"
        f" 最后页面={str(destination_state.get('href') or '')[:180]}"
        f" 校验={last_validation_error}"
    )


def ensure_sample_request_context(
    store_id: str,
    *,
    force_reload: bool = False,
    navigation_timeout: float = 90.0,
    poll_interval: float = 1.5,
    navigate_page_fn: Callable[[str, str], dict[str, Any]] = schedule_page_navigation,
    execute_script_fn: Callable[..., Any] = zclaw_exec,
) -> dict[str, Any]:
    """从已登录商家中心任意子页跳到样品申请，并等到 URL 带上 shop_id。

    空白页和登录页拒绝。个别页 execute_script 失败或跳转被拦住时超时退出。
    实测样品申请页 SPA 加载期 execute_script 可阻塞 5–24s、到 complete 约
    60s，预算必须覆盖加载期（45s 时探测全部超时、连 href 稳定都等不到）。
    """
    initial_state = execute_script_fn(
        store_id,
        INSPECT_NAVIGATION_PAGE_JS,
        timeout=30,
        retries=1,
    )
    if not isinstance(initial_state, dict):
        raise RuntimeError(f"无法识别当前店铺页面: {initial_state!r}"[:300])
    initial_page_type = validate_navigation_start(initial_state)

    if not force_reload:
        try:
            destination_context = validate_sample_request_destination(initial_state)
            return {
                "ok": True,
                "already": True,
                "initial_page_type": initial_page_type,
                "destination": destination_context,
            }
        except RuntimeError:
            pass

    # 筛查显式导航始终访问规范 URL，避免上次扫表停在中间分页后从该页续扫。
    navigation_deadline = deadline_from_timeout(max(1.0, navigation_timeout))
    navigate_page_fn(store_id, SAMPLE_REQUEST_URL)
    if not sleep_until_next_probe(
        navigation_deadline,
        max(2.0, poll_interval) if force_reload else max(2.0, min(poll_interval, 4.0)),
    ):
        raise RuntimeError("样品申请页导航 settle 等待耗尽 deadline")
    destination_context = _wait_for_sample_request_destination(
        store_id,
        navigation_timeout=navigation_timeout,
        poll_interval=poll_interval,
        execute_script_fn=execute_script_fn,
    )
    return {
        "ok": True,
        "already": False,
        "initial_page_type": initial_page_type,
        "destination": destination_context,
    }


def navigate_from_seller_home_to_pending(
    store_id: str,
    *,
    navigation_timeout: float = 90.0,
    poll_interval: float = 1.5,
    navigate_page_fn: Callable[[str, str], dict[str, Any]] = schedule_page_navigation,
    execute_script_fn: Callable[..., Any] = zclaw_exec,
    ensure_pending_fn: Callable[..., dict[str, Any]] = assert_on_pending_list,
) -> dict[str, Any]:
    """显式开启时，从已登录商家中心任意子页导航并切到待审核 tab。"""
    navigation_result = ensure_sample_request_context(
        store_id,
        force_reload=True,
        navigation_timeout=navigation_timeout,
        poll_interval=poll_interval,
        navigate_page_fn=navigate_page_fn,
        execute_script_fn=execute_script_fn,
    )

    pending_result = ensure_pending_fn(
        store_id,
        page_wait=max(0.5, poll_interval),
        retries=4,
    )
    readiness_deadline = deadline_from_timeout(max(5.0, navigation_timeout))
    readiness_state: dict[str, Any] = {}
    while remaining_seconds(readiness_deadline) > 0:
        try:
            candidate_readiness = execute_script_fn(
                store_id,
                INSPECT_PENDING_LIST_READINESS_JS,
                timeout=HREF_PROBE_TIMEOUT_SECONDS,
                retries=0,
            )
        except Exception:
            candidate_readiness = None
        if isinstance(candidate_readiness, dict):
            readiness_state = candidate_readiness
            if candidate_readiness.get("ready"):
                break
        if not sleep_until_next_probe(readiness_deadline, max(0.2, poll_interval)):
            break
    else:
        raise RuntimeError(
            "已进入待审核，但列表数据在超时前未就绪。"
            f" href={str(readiness_state.get('href') or '')[:180]}"
            f" rows={readiness_state.get('row_count')}"
        )

    return {
        "ok": True,
        "initial_page_type": navigation_result["initial_page_type"],
        "destination": navigation_result["destination"],
        "pending_tab": pending_result,
        "list_readiness": readiness_state,
    }
