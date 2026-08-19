#!/usr/bin/env python3
"""样品申请「已发货」列表：只读扫表。

未在样品申请页时，从已登录商家中心跳到样品申请并等到 shop_id。
本模块只点「已发货」并翻页。绝不点同意 / 拒绝 / 发货。
"""
from __future__ import annotations

import logging

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from .sample_dom import click_next
from .sample_navigation import ensure_sample_request_context
from .zclaw import zclaw_exec

logger = logging.getLogger(__name__)

ENSURE_SHIPPED_TAB_JS = r"""
(() => {
  const text = document.body.innerText || '';
  const onSample = /样品申请|Sample Requests|sample-request/i.test(location.href + text);
  if (!onSample) {
    return JSON.stringify({
      ok: false,
      reason: 'not-on-sample-page',
      href: location.href,
      hint: '请先打开：联盟中心 → 样品申请'
    });
  }
  const titles = [...document.querySelectorAll('.core-tabs-header-title, [role=tab]')];
  const active = titles.find(e =>
    e.classList && e.classList.contains('core-tabs-header-title-active')
    && /已发货|Shipped/i.test((e.innerText || '').trim())
  );
  if (active) {
    return JSON.stringify({
      ok: true,
      already: true,
      text: (active.innerText || '').trim().slice(0, 40),
      href: location.href
    });
  }
  const tab = titles.find(e => /已发货|Shipped/i.test((e.innerText || '').trim()));
  if (!tab) {
    return JSON.stringify({ok: false, reason: 'no-shipped-tab', href: location.href});
  }
  tab.click();
  return JSON.stringify({
    ok: true,
    clicked: true,
    text: (tab.innerText || '').trim().slice(0, 40),
    href: location.href
  });
})()
"""

EXTRACT_SHIPPED_JS = r"""
(() => {
  function getRecord(tr) {
    const key = Object.keys(tr || {}).find(k =>
      k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
    );
    if (!key) return null;
    let fiber = tr[key];
    for (let i = 0; i < 60 && fiber; i++) {
      const props = fiber.memoizedProps;
      if (props && props.record) return props.record;
      fiber = fiber.return;
    }
    return null;
  }
  function creatorOf(record) {
    if (record && record.creator_info) return record.creator_info;
    try { return record.agg_info.apply_group.creator_info; } catch (e) { return {}; }
  }
  const rows = [];
  const seen = new Set();
  for (const tr of document.querySelectorAll('table tbody tr')) {
    const record = getRecord(tr) || {};
    const creator = creatorOf(record) || {};
    const name = creator.name || record.name || '';
    const applyId = record.apply_id != null ? String(record.apply_id) : '';
    if (!name && !applyId) continue;
    const key = applyId || (name + '|' + String(record.product_id || ''));
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      apply_id: applyId,
      product_id: record.product_id != null ? String(record.product_id) : '',
      product_title: record.product_title || '',
      sku_id: record.sku_id != null ? String(record.sku_id) : '',
      sku_desc: record.sku_desc || '',
      main_order_id: record.main_order_id != null ? String(record.main_order_id) : '',
      curr_status: record.curr_status,
      review_status: record.review_status,
      creator_id: String(creator.creator_id || record.creator_id || ''),
      creator_name: name,
      nick_name: creator.nick_name || record.nick_name || '',
      href: location.href
    });
  }
  const active = document.querySelector(
    '.core-pagination-item-active, .arco-pagination-item-active'
  );
  const next = document.querySelector(
    '.core-pagination-item-next:not(.core-pagination-item-disabled), ' +
    '.arco-pagination-item-next:not(.arco-pagination-item-disabled), ' +
    'li[aria-label="下一页"]:not([aria-disabled="true"]), ' +
    'button[aria-label="下一页"]:not([disabled])'
  );
  const nextDisabled = !next ||
    (next.classList && (
      next.classList.contains('core-pagination-item-disabled') ||
      next.classList.contains('arco-pagination-item-disabled')
    )) ||
    next.getAttribute('aria-disabled') === 'true' ||
    next.hasAttribute('disabled');
  return JSON.stringify({
    href: location.href,
    page: active ? (active.innerText || '').trim() : '',
    count: rows.length,
    rows,
    canNext: !nextDisabled
  });
})()
"""


def shop_id_from_href(href: str) -> str:
    parsed = urlparse(href or "")
    values = parse_qs(parsed.query).get("shop_id") or []
    return (values[0] if values else "").strip()


def ensure_sample_page_loaded(store_id: str, *, page_wait: float = 2.0) -> dict[str, Any]:
    """只保证样品申请页带 shop_id，不点击「已发货」tab。API 读取走 tab=30。"""
    result = ensure_sample_request_context(
        store_id,
        force_reload=False,
        navigation_timeout=max(20.0, page_wait + 15.0),
        poll_interval=max(0.5, min(2.0, page_wait)),
    )
    href = str((result.get("destination") or {}).get("href") or "")
    return {
        "ok": True,
        "href": href,
        "clicked": False,
        "already": bool(result.get("already")),
        "destination": result.get("destination") or {},
    }


def ensure_on_sample_page(store_id: str, *, page_wait: float = 2.0) -> dict[str, Any]:
    """若已在样品申请域则只切 tab；否则跳到样品申请并等到 shop_id。"""
    ensure_sample_request_context(
        store_id,
        force_reload=False,
        navigation_timeout=max(20.0, page_wait + 15.0),
        poll_interval=max(0.5, min(2.0, page_wait)),
    )
    tab = zclaw_exec(store_id, ENSURE_SHIPPED_TAB_JS)
    if not isinstance(tab, dict) or not tab.get("ok"):
        raise RuntimeError(f"无法切到「已发货」tab: {tab}")
    if tab.get("clicked"):
        time.sleep(page_wait)
    return tab


def scrape_shipped_list(
    store_id: str,
    *,
    max_pages: int = 50,
    max_rows: int = 0,
    page_wait: float = 2.0,
) -> list[dict[str, Any]]:
    ensure_on_sample_page(store_id, page_wait=page_wait)
    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page_i in range(max_pages):
        data = zclaw_exec(store_id, EXTRACT_SHIPPED_JS)
        if not isinstance(data, dict):
            raise RuntimeError(f"extract shipped bad: {data!r}"[:300])
        rows = data.get("rows") or []
        logger.info(
            f"[已发货] page={data.get('page') or page_i + 1} "
            f"rows={len(rows)} href={(data.get('href') or '')[:90]}")
        if page_i == 0 and not rows:
            logger.info("[已发货] 当前 tab 0 行")
            return []
        for row in rows:
            key = (
                str(row.get("apply_id") or "")
                or f"{row.get('creator_name')}|{row.get('main_order_id')}|{row.get('product_id')}"
            )
            if key in seen:
                continue
            seen.add(key)
            row["_list_href"] = data.get("href") or ""
            row["_shop_id"] = shop_id_from_href(str(data.get("href") or ""))
            all_rows.append(row)
            if max_rows and len(all_rows) >= max_rows:
                logger.info(f"[已发货] 达到 max_rows={max_rows}，停止")
                return all_rows
        if not data.get("canNext"):
            break
        nxt = click_next(store_id, page_wait=page_wait)
        if not nxt.get("ok"):
            break
    logger.info(f"[已发货] 完成，去重后 {len(all_rows)} 行")
    return all_rows
