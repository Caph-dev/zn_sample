#!/usr/bin/env python3
"""商家中心订单页：按 main_order_id 只读抽取 TikTok 物流单号。"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from .tracking_parse import parse_tiktok_logistics
from .zclaw import visit_page, zclaw_exec

EXTRACT_ORDER_JS = r"""
(() => {
  const text = ((document.body && document.body.innerText) || '').replace(/\u00a0/g, ' ');
  const hits = [...document.querySelectorAll('div,span')].filter(el => {
    const t = (el.innerText || '').trim();
    return /TikTok\s*物流/.test(t) && t.length < 80;
  }).slice(0, 8).map(el => (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80));
  return JSON.stringify({
    href: location.href,
    title: document.title || '',
    ready: document.readyState,
    hits,
    text: text.slice(0, 6000)
  });
})()
"""


def order_lookup_url(order_id: str, *, shop_id: str = "", shop_region: str = "US") -> str:
    oid = quote(str(order_id).strip(), safe="")
    url = (
        "https://seller.tiktokshopglobalselling.com/order"
        f"?main_order_id[]={oid}&order_status[]=200&order_tag[]=8"
        f"&shop_region={quote(shop_region)}&tab=all"
    )
    if shop_id:
        url += f"&shop_id={quote(shop_id)}"
    return url


def fetch_tiktok_tracking(
    store_id: str,
    order_id: str,
    *,
    shop_id: str = "",
    wait: float = 4.0,
    retries: int = 2,
) -> dict[str, Any]:
    """visit 订单页，抽 TikTok 物流单号。不点打印/发货。"""
    oid = str(order_id or "").strip()
    if not oid:
        return {"ok": False, "error": "empty-order-id"}
    url = order_lookup_url(oid, shop_id=shop_id)
    visit_page(store_id, url)
    time.sleep(wait)
    last: dict[str, Any] | None = None
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        ret = zclaw_exec(store_id, EXTRACT_ORDER_JS)
        if not isinstance(ret, dict):
            last = {"ok": False, "error": f"bad-extract:{ret!r}"[:200]}
            time.sleep(1.5)
            continue
        parsed = parse_tiktok_logistics(str(ret.get("text") or ""), order_id=oid)
        last = {
            "ok": bool(parsed.get("tracking_no")),
            "order_id": oid,
            "tracking_raw": parsed.get("tracking_raw") or "",
            "tracking_no": parsed.get("tracking_no") or "",
            "via": parsed.get("via") or "",
            "href": ret.get("href") or "",
            "hits": ret.get("hits") or [],
        }
        if last["ok"]:
            return last
        if attempt + 1 < attempts:
            time.sleep(1.8)
    assert last is not None
    if not last.get("ok"):
        last["error"] = last.get("error") or "no-tiktok-tracking"
    return last
