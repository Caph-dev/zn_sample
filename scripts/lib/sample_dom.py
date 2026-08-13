#!/usr/bin/env python3
"""样品申请「待审核」列表：假设用户已打开并定位到该页。

纪律：
  - **不**负责导航联盟中心 / 样品申请入口
  - 只校验当前页、必要时点「待审核」tab、扫表翻页
  - **绝不**点击「同意 / 批准 / Approve / 拒绝」
"""
from __future__ import annotations

import time
from typing import Any

from .zclaw import zclaw_exec

ENSURE_PENDING_TAB_JS = r"""
(() => {
  const text = document.body.innerText || '';
  const onSample = /样品申请|Sample Requests|sample-request/i.test(location.href + text);
  if (!onSample) {
    return JSON.stringify({
      ok: false,
      reason: 'not-on-sample-page',
      href: location.href,
      hint: '请先手动打开并停留在：联盟中心 → 样品申请 → 待审核'
    });
  }
  const titles = [...document.querySelectorAll('.core-tabs-header-title, [role=tab]')];
  const active = titles.find(e =>
    e.classList && e.classList.contains('core-tabs-header-title-active')
  );
  const activeText = active ? (active.innerText || '').trim() : '';
  if (/待审核|To\\s*review|Pending/i.test(activeText)) {
    return JSON.stringify({ok: true, already: true, text: activeText.slice(0, 40), href: location.href});
  }
  const t = titles.find(e => /待审核|To\\s*review|Pending/i.test((e.innerText || '').trim()));
  if (!t) {
    // 宽松
    const loose = [...document.querySelectorAll('div,span,a,li')].find(e => {
      const tx = (e.innerText || '').trim();
      return /待审核/.test(tx) && tx.length < 30 && e.offsetParent;
    });
    if (!loose) {
      return JSON.stringify({
        ok: false,
        reason: 'no-pending-tab',
        href: location.href,
        hint: '请手动点到「待审核」tab'
      });
    }
    loose.click();
    return JSON.stringify({ok: true, clicked: true, text: (loose.innerText || '').trim().slice(0, 40), href: location.href});
  }
  t.click();
  return JSON.stringify({ok: true, clicked: true, text: (t.innerText || '').trim().slice(0, 40), href: location.href});
})()
"""

EXTRACT_LIST_JS = r"""
(() => {
  function getRecord(tr) {
    const key = Object.keys(tr || {}).find(k =>
      k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
    );
    if (!key) return null;
    let f = tr[key];
    for (let i = 0; i < 50 && f; i++) {
      const p = f.memoizedProps;
      if (p && p.record) return p.record;
      f = f.return;
    }
    return null;
  }
  function creatorOf(r) {
    if (r && r.creator_info) return r.creator_info;
    try { return r.agg_info.apply_group.creator_info; } catch (e) { return {}; }
  }
  // 禁止扫到「操作」列文案当数据
  const trs = [...document.querySelectorAll('table tbody tr')];
  const rows = [];
  for (let idx = 0; idx < trs.length; idx++) {
    const tr = trs[idx];
    const r = getRecord(tr) || {};
    const c = creatorOf(r) || {};
    const tds = [...tr.querySelectorAll('td')];
    const cell = (i) => ((tds[i] && tds[i].innerText) || '').trim();
    // 展开后的子行：无达人信息，跳过
    const name = c.name || '';
    if (!name && !r.apply_id) continue;
    // 若整行主要是「同意/拒绝」且无 creator，跳过
    const rowText = (tr.innerText || '').trim();
    if (/^同意|^拒绝/.test(rowText) && !name) continue;

    rows.push({
      row_index: idx,
      apply_id: r.apply_id != null ? String(r.apply_id) : '',
      apply_ids: r.apply_ids || [],
      product_id: r.product_id != null ? String(r.product_id) : '',
      product_title: r.product_title || '',
      sku_id: r.sku_id != null ? String(r.sku_id) : '',
      sku_desc: r.sku_desc || '',
      apply_count: r.apply_count,
      can_be_approved: r.can_be_approved,
      review_status: r.review_status,
      creator_id: c.creator_id != null ? String(c.creator_id) : '',
      tt_uid: c.tt_uid != null ? String(c.tt_uid) : '',
      creator_name: name,
      nick_name: c.nick_name || '',
      follower_num: c.follower_num,
      gmv: c.gmv,
      item_sold: c.item_sold,
      fulfillment_rate: c.fulfillment_rate,
      content_video_views: c.content_video_views,
      pps_score: c.pps_score,
      categories: c.categories || [],
      top_follower_gender: c.top_follower_gender || [],
      top_follower_ages: c.top_follower_ages || [],
      cell_name: cell(2),
      cell_fulfill: cell(4),
      cell_video_views: cell(5),
      cell_gmv: cell(6),
      cell_units: cell(7),
      href: location.href
    });
  }

  const text = document.body.innerText || '';
  const pending = (text.match(/待审核\s*(\d+)/) || [])[1] || '';
  const active = document.querySelector('.core-pagination-item-active, .arco-pagination-item-active');
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
    title: document.title || '',
    pending_count: pending,
    page: active ? (active.innerText || '').trim() : '',
    count: rows.length,
    rows,
    canNext: !nextDisabled
  });
})()
"""

CLICK_NEXT_JS = r"""
(() => {
  const next = document.querySelector(
    '.core-pagination-item-next:not(.core-pagination-item-disabled), ' +
    '.arco-pagination-item-next:not(.arco-pagination-item-disabled), ' +
    'li[aria-label="下一页"]:not([aria-disabled="true"]), ' +
    'button[aria-label="下一页"]:not([disabled])'
  );
  if (!next) return JSON.stringify({ok:false, reason:'no-next'});
  // 禁止误点同意
  const t = (next.innerText || '') + (next.getAttribute('aria-label') || '');
  if (/同意|批准|Approve|拒绝|Reject/.test(t)) {
    return JSON.stringify({ok:false, reason:'forbidden-control', t});
  }
  next.click();
  return JSON.stringify({ok:true});
})()
"""


def assert_on_pending_list(store_id: str, *, page_wait: float = 1.5) -> dict:
    """校验已在样品申请页，并确保「待审核」tab。不打开入口页。"""
    tab = zclaw_exec(store_id, ENSURE_PENDING_TAB_JS)
    if not isinstance(tab, dict) or not tab.get("ok"):
        raise RuntimeError(
            "当前不在「样品申请-待审核」或无法切换 tab。"
            f" 请手动打开并定位到该页后重试。详情: {tab}"
        )
    if tab.get("clicked"):
        time.sleep(page_wait)
    return tab


def extract_page(store_id: str) -> dict:
    ret = zclaw_exec(store_id, EXTRACT_LIST_JS)
    if not isinstance(ret, dict):
        raise RuntimeError(f"extract_page bad result: {ret!r}"[:400])
    return ret


def click_next(store_id: str, *, page_wait: float = 2.0) -> dict:
    ret = zclaw_exec(store_id, CLICK_NEXT_JS)
    if isinstance(ret, dict) and ret.get("ok"):
        time.sleep(page_wait)
    return ret if isinstance(ret, dict) else {"ok": False, "raw": ret}


def scrape_pending_list(
    store_id: str,
    *,
    max_pages: int = 50,
    max_rows: int = 0,
    page_wait: float = 2.0,
    ensure_tab: bool = True,
) -> list[dict]:
    """扫当前「待审核」列表。用户须已打开该页。"""
    if ensure_tab:
        assert_on_pending_list(store_id, page_wait=max(page_wait, 1.5))

    all_rows: list[dict] = []
    seen: set[str] = set()
    list_href = ""

    for page_i in range(max_pages):
        data = extract_page(store_id)
        rows = data.get("rows") or []
        list_href = data.get("href") or list_href
        print(
            f"[扫表] page={data.get('page') or page_i + 1} "
            f"rows={len(rows)} pending={data.get('pending_count')} "
            f"href={(data.get('href') or '')[:90]}"
        )
        if page_i == 0 and not rows:
            raise RuntimeError(
                "当前页未读到待审核行。请确认：已打开「样品申请」且在「待审核」tab，列表已加载。"
            )
        for r in rows:
            key = (
                str(r.get("apply_id") or "")
                or f"{r.get('creator_name')}|{r.get('product_id')}|{r.get('sku_id')}"
            )
            if key in seen:
                continue
            seen.add(key)
            r["_list_href"] = list_href
            all_rows.append(r)
            if max_rows and len(all_rows) >= max_rows:
                print(f"[扫表] 达到 max_rows={max_rows}，停止")
                return all_rows
        if not data.get("canNext"):
            break
        nxt = click_next(store_id, page_wait=page_wait)
        if not nxt.get("ok"):
            break
    print(f"[扫表] 完成，去重后 {len(all_rows)} 行")
    return all_rows
