"""Source-proven target-invitation navigation, without store lifecycle operations.

Origin: zn_daren/scripts/lib/target_navigation.py at 2d8e21c (unchanged in
the confirmed 2026-10-05 worktree). Only target lib.zclaw supplies transport.
No visit_page, list_stores, default store, open/close or runtime reopen path.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from .target_invitation_dom import (
    ONGOING_TAB_LOOKUP_JS, _guard_recovery_script,
    _is_transient_read_error, _read_recovery_state, ensure_ongoing_tab, restore_ongoing_list,
)
from .zclaw import zclaw_exec

logger = logging.getLogger(__name__)

AFFILIATE_CENTER_HOST = "affiliate.tiktokshopglobalselling.com"
TARGET_INVITATION_PATH = "/affiliate/collaboration/target-invitation"
LEGACY_TARGET_INVITATION_PATH = "/connection/target-invitation"
NAVIGATION_TIMEOUT_SECONDS = 90.0

INSPECT_NAVIGATION_PAGE_JS = r"""
(() => {
  const href = location.href || '';
  const hostname = location.hostname || '';
  const pathname = location.pathname || '';
  const title = document.title || '';
  const bodyText = (document.body && document.body.innerText || '').slice(0, 12000);
  const parameters = new URLSearchParams(location.search || '');
  let shopId = (parameters.get('shop_id') || '').trim();
  let shopRegion = (parameters.get('shop_region') || '').trim().toUpperCase();
  const pickShop = accountInfo => {
    if (!accountInfo || typeof accountInfo !== 'object') return;
    const shop = accountInfo.shop || {};
    const account = accountInfo.account || {};
    if (!shopId) shopId = String(shop.shop_id || account.account_id || '').trim();
    if (!shopRegion) shopRegion = String(shop.region || '').trim().toUpperCase();
  };
  try {
    const parsed = JSON.parse(localStorage.getItem('ecom_seller_base_account_info') || 'null');
    if (parsed) { pickShop(parsed); pickShop(parsed.value); pickShop(parsed.data); pickShop(parsed.value && parsed.value.data); }
  } catch (error) {}
  try {
    if (!shopRegion) shopRegion = String(localStorage.getItem('current_shop_region')
      || localStorage.getItem('-affiliate-selected-shop-region') || '').trim().toUpperCase();
  } catch (error) {}
  const targetPage = hostname === 'affiliate.tiktokshopglobalselling.com' && /\/target-invitation\/?$/i.test(pathname);
  const affiliatePage = hostname === 'affiliate.tiktokshopglobalselling.com';
  const sellerHost = /^seller(?:\.[a-z0-9-]+)*\.tiktokshopglobalselling\.com$/i.test(hostname)
    || /^seller(?:\.[a-z0-9-]+)*\.tiktokglobalshop\.com$/i.test(hostname)
    || /^seller(?:\.[a-z0-9-]+)*\.tiktok\.com$/i.test(hostname);
  const loginPage = /login|sign[-_]?in|passport/i.test(pathname + ' ' + title)
    || /Log in to TikTok Shop|登录 TikTok Shop|Sign in to TikTok Shop/i.test(bodyText);
  return JSON.stringify({ok:true, href, hostname, pathname, title:title.slice(0,160),
    ready_state:document.readyState, shop_id:shopId, shop_region:shopRegion,
    page_type:loginPage ? 'login' : (targetPage ? 'target-invitation'
      : (sellerHost ? 'seller-center' : (affiliatePage ? 'affiliate-center' : 'unknown')))});
})()
"""

INSPECT_TARGET_LIST_READINESS_JS = r"""
(() => {
  const bodyText = document.body && document.body.innerText || '';
  const rowCount = document.querySelectorAll('table tbody tr').length;
  const hasEmptyState = /暂无数据|No data|没有数据/i.test(bodyText);
  const ongoingTab = [...document.querySelectorAll('.core-tabs-header-title, [role=tab]')].find(element => {
    const text = (element.innerText || '').trim().replace(/\s+/g, ' ');
    return (/^(进行中|Ongoing)\b/i.test(text) || text === '进行中' || /^进行中\s*\d+/.test(text))
      && element.getBoundingClientRect().width > 0;
  });
  const hasOngoingTab = !!ongoingTab;
  const ongoing = !!ongoingTab && (ongoingTab.classList.contains('core-tabs-header-title-active')
    || ongoingTab.getAttribute('aria-selected') === 'true');
  const hasPagination = !!document.querySelector(
    '.core-pagination-item-next, .arco-pagination-item-next, .core-pagination-item-active, .arco-pagination-item-active'
  );
  const listError = /哎呀[！!]\s*出错了|网络错误|请重试|Network error|Something went wrong/i.test(bodyText);
  const ready = !!document.body && document.readyState === 'complete' && !listError
    && hasOngoingTab && (hasPagination || hasEmptyState);
  return JSON.stringify({ok:true, href:location.href || '', ready_state:document.readyState,
    row_count:rowCount, has_empty_state:hasEmptyState, has_ongoing_tab:hasOngoingTab,
    has_pagination:hasPagination, list_error:listError, ongoing, ready});
})()
"""

# Reuse the proven visible tab finder, not a text-only match or a clicking script.
INSPECT_TARGET_SHELL_JS = _guard_recovery_script("(() => {\n" + ONGOING_TAB_LOOKUP_JS + r"""
  return JSON.stringify({ok:true, href:location.href,
    shell_ready:!!document.body && document.readyState === 'complete' && !!ongoingTab});
})()
""")
INSPECT_TARGET_LIST_READINESS_JS = _guard_recovery_script(INSPECT_TARGET_LIST_READINESS_JS)


def target_invitation_url(
    *, shop_id: str = "", shop_region: str = "US", path: str = TARGET_INVITATION_PATH,
) -> str:
    if path not in {TARGET_INVITATION_PATH, LEGACY_TARGET_INVITATION_PATH}:
        raise ValueError("Unsupported target invitation path")
    query = {"shop_region": str(shop_region or "US").strip().upper() or "US"}
    normalized_shop_id = str(shop_id or "").strip()
    if normalized_shop_id:
        query["shop_id"] = normalized_shop_id
    return f"https://{AFFILIATE_CENTER_HOST}{path}?{urllib.parse.urlencode(query)}"


def resolve_target_invitation_url(*, shop_id: str = "", shop_region: str = "US") -> str:
    """Use the proven legacy route when the new route lacks required shop_id."""
    return target_invitation_url(
        shop_id=shop_id, shop_region=shop_region,
        path=TARGET_INVITATION_PATH if str(shop_id or "").strip() else LEGACY_TARGET_INVITATION_PATH,
    )


def is_target_invitation_path(path: str) -> bool:
    return urllib.parse.unquote(str(path or "")).rstrip("/") in {
        TARGET_INVITATION_PATH, LEGACY_TARGET_INVITATION_PATH,
    }


def is_target_invitation_href(href: str) -> bool:
    parsed = urllib.parse.urlsplit(href or "")
    return (parsed.scheme == "https" and parsed.hostname == AFFILIATE_CENTER_HOST
            and is_target_invitation_path(parsed.path))


def validate_navigation_start(page_state: dict[str, Any]) -> str:
    href = str(page_state.get("href") or "").strip()
    page_type = str(page_state.get("page_type") or "")
    if not href or href == "about:blank":
        raise RuntimeError("Store page is about:blank; log into Seller Center first")
    if page_type == "login":
        raise RuntimeError("TikTok Shop login page; complete login manually")
    parsed = urllib.parse.urlsplit(href)
    hostname = parsed.hostname or ""
    seller_host = re.fullmatch(
        r"seller(?:\.[a-z0-9-]+)*\.(?:tiktokshopglobalselling|tiktokglobalshop|tiktok)\.com", hostname,
    )
    if parsed.scheme != "https" or not (seller_host or hostname == AFFILIATE_CENTER_HOST):
        raise RuntimeError(f"Unrecognized Seller/Affiliate Center page: {href[:180]}")
    if page_type not in {"seller-center", "affiliate-center", "target-invitation"}:
        raise RuntimeError(f"Unrecognized Seller/Affiliate Center page: {href[:180]}")
    return page_type


def validate_target_invitation_destination(page_state: dict[str, Any]) -> dict[str, str]:
    href = str(page_state.get("href") or "").strip()
    if page_state.get("page_type") == "login" or not is_target_invitation_href(href):
        raise RuntimeError(f"Not at target invitation list: {href[:180]}")
    parsed = urllib.parse.urlsplit(href)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for field in ("shop_id", "shop_region"):
        if len(query.get(field) or []) != 1 or not query[field][0].strip():
            raise RuntimeError(f"Target invitation URL requires one {field}: {href[:180]}")
    return {"href": href, "shop_id": query["shop_id"][0].strip(), "shop_region": query["shop_region"][0].strip().upper()}


def shop_context_from_href(href: str) -> tuple[str, str]:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(href or "").query)
    return ((query.get("shop_id") or [""])[0].strip(),
            (query.get("shop_region") or ["US"])[0].strip().upper() or "US")


def shop_context_from_page_state(page_state: dict[str, Any]) -> tuple[str, str]:
    href_shop_id, href_shop_region = shop_context_from_href(str(page_state.get("href") or ""))
    return (str(page_state.get("shop_id") or "").strip() or href_shop_id,
            str(page_state.get("shop_region") or "").strip().upper() or href_shop_region)


def current_page_href(store_id: str, *, execute_script_fn: Callable[..., Any] | None = None) -> str:
    execute_script = execute_script_fn or zclaw_exec
    result = execute_script(store_id, "(() => JSON.stringify({href:location.href || ''}))()", timeout=15)
    return str(result.get("href") or "").strip() if isinstance(result, dict) else ""


def schedule_page_navigation(
    store_id: str, url: str, *, execute_script_fn: Callable[..., Any] | None = None, deadline: float | None = None,
) -> dict[str, Any]:
    if not is_target_invitation_href(url):
        raise ValueError("Navigation only accepts the proven target invitation URLs")
    execute_script = execute_script_fn or zclaw_exec
    script = f"""
(() => {{
  const targetUrl = {json.dumps(url, ensure_ascii=False)};
  window.setTimeout(() => window.location.assign(targetUrl), 100);
  return JSON.stringify({{ok:true, scheduled:true, target_url:targetUrl}});
}})()
"""
    options = {"timeout": 30} if deadline is None else {
        "timeout": 15, "deadline": deadline, "retries": 0, "retry_timeout_expired": False,
    }
    if deadline is not None and deadline - time.monotonic() < 0.5:
        raise RuntimeError("Target navigation deadline expired")
    result = execute_script(store_id, script, **options)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(f"Unable to schedule target navigation: {result!r}"[:300])
    return result


def wait_for_target_list_ready(
    store_id: str, *, timeout: float = NAVIGATION_TIMEOUT_SECONDS, poll_interval: float = 1.5,
    execute_script_fn: Callable[..., Any] | None = None,
    deadline: float | None = None, expected_context=None, require_ongoing: bool = False,
) -> dict[str, Any]:
    execute_script = execute_script_fn or zclaw_exec
    deadline = deadline if deadline is not None else time.monotonic() + max(1.0, timeout)
    last_state: dict[str, Any] = {}
    while deadline - time.monotonic() >= 0.5:
        candidate = _read_recovery_state(store_id, INSPECT_TARGET_LIST_READINESS_JS, context=expected_context,
                                         deadline=deadline, execute_script_fn=execute_script)
        last_state = candidate
        context = validate_target_invitation_destination(candidate)
        if expected_context is not None and (AFFILIATE_CENTER_HOST, context["shop_id"], context["shop_region"]) != expected_context:
            raise RuntimeError("Platform shop changed while waiting for target list")
        if type(candidate.get("ready")) is not bool or (require_ongoing and type(candidate.get("ongoing")) is not bool):
            raise RuntimeError("Malformed target list readiness")
        if candidate["ready"] and (not require_ongoing or candidate["ongoing"]):
            return candidate
        time.sleep(min(max(0.2, poll_interval), max(0.0, deadline - time.monotonic())))
    raise RuntimeError(f"Target invitation list readiness timed out: {last_state!r}"[:400])


def wait_for_target_shell(store_id: str, *, deadline: float, expected_context=None,
                          execute_script_fn=None, poll_interval: float = 1.5) -> dict[str, Any]:
    while deadline - time.monotonic() >= 0.5:
        state = _read_recovery_state(store_id, INSPECT_TARGET_SHELL_JS, deadline=deadline,
                                context=expected_context, execute_script_fn=execute_script_fn)
        validate_target_invitation_destination(state)
        if type(state.get("shell_ready")) is not bool:
            raise RuntimeError("Malformed target invitation shell")
        if state.get("shell_ready") is True:
            return state
        time.sleep(min(max(0.2, poll_interval), max(0.0, deadline - time.monotonic())))
    raise RuntimeError("Target invitation shell readiness timed out")


def navigate_from_seller_home_to_ongoing(
    store_id: str, *, navigation_timeout: float = NAVIGATION_TIMEOUT_SECONDS, poll_interval: float = 1.5,
    navigate_page_fn: Callable[[str, str], dict[str, Any]] | None = None,
    execute_script_fn: Callable[..., Any] | None = None,
    ensure_ongoing_fn: Callable[..., dict[str, Any]] | None = None,
    wait_ready_fn: Callable[..., dict[str, Any]] | None = None,
    wait_shell_fn: Callable[..., dict[str, Any]] | None = None,
    restore_list_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Navigate an already logged-in store; failures never restart or switch it."""
    execute_script = execute_script_fn or zclaw_exec
    deadline = time.monotonic() + max(1.0, navigation_timeout)
    initial_state = execute_script(store_id, INSPECT_NAVIGATION_PAGE_JS, timeout=15,
                                   deadline=deadline, retries=0, retry_timeout_expired=False)
    if not isinstance(initial_state, dict):
        raise RuntimeError(f"Unable to identify store page: {initial_state!r}"[:300])
    initial_page_type = validate_navigation_start(initial_state)
    shop_id, shop_region = shop_context_from_page_state(initial_state)
    current_href = str(initial_state.get("href") or "")
    destination_context: dict[str, str] | None = None
    if is_target_invitation_href(current_href):
        try:
            destination_context = validate_target_invitation_destination(initial_state)
        except RuntimeError:
            pass
    already = destination_context is not None
    target_url = resolve_target_invitation_url(shop_id=shop_id, shop_region=shop_region)
    if not already:
        logger.info("Navigating to target invitation list: %s", target_url)
        navigator = navigate_page_fn or (
            lambda store_identifier, url: schedule_page_navigation(store_identifier, url, execute_script_fn=execute_script,
                                                                   deadline=deadline)
        )
        navigator(store_id, target_url)
        time.sleep(min(max(0.2, poll_interval), max(0.0, deadline - time.monotonic())))
        last_error = ""
        while deadline - time.monotonic() >= 0.5:
            try:
                candidate = execute_script(store_id, INSPECT_NAVIGATION_PAGE_JS, timeout=15,
                                           deadline=deadline, retries=0, retry_timeout_expired=False)
            except Exception as error:
                if not _is_transient_read_error(error):
                    raise
                last_error = str(error)
                time.sleep(min(max(0.2, poll_interval), max(0.0, deadline - time.monotonic())))
                continue
            if not isinstance(candidate, dict) or candidate.get("ok") is not True:
                raise RuntimeError("Malformed target navigation page")
            validate_navigation_start(candidate)
            if is_target_invitation_href(str(candidate.get("href") or "")):
                try:
                    destination_context = validate_target_invitation_destination(candidate)
                    break
                except RuntimeError as error:
                    # The platform may still be adding shop context during route migration.
                    last_error = str(error)
            time.sleep(min(max(0.2, poll_interval), max(0.0, deadline - time.monotonic())))
        if destination_context is None:
            raise RuntimeError(f"Target navigation timed out: {target_url}; {last_error}")
    if shop_id and destination_context["shop_id"] != shop_id:
        raise RuntimeError("Platform shop_id changed during target navigation")
    if destination_context["shop_region"] != shop_region:
        raise RuntimeError("Platform shop_region changed during target navigation")
    restored = (restore_list_fn or restore_ongoing_list)(
        store_id, expected_context=(AFFILIATE_CENTER_HOST, destination_context["shop_id"], destination_context["shop_region"]),
        deadline=deadline, execute_script_fn=execute_script, ensure_ongoing_fn=ensure_ongoing_fn or ensure_ongoing_tab,
        wait_ready_fn=wait_ready_fn, wait_shell_fn=wait_shell_fn,
    )
    if not isinstance(restored, dict) or restored.get("nav", {}).get("ok") is not True:
        raise RuntimeError("Target invitation list recovery not verified")
    return {"ok": True, "already": already, "initial_page_type": initial_page_type,
            "destination": destination_context, "ongoing_tab": restored["tab"], "target_url": target_url}
