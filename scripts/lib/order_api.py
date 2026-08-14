#!/usr/bin/env python3
"""商家订单页物流查询 API 适配器。"""
from __future__ import annotations

import re
import time
from collections.abc import Iterable
from typing import Any

from .order_dom import order_lookup_url
from .page_api import (
    SELLER_LOGISTICS_ENDPOINT,
    PageApiSchemaError,
    get_seller_page_context,
    get_seller_read_json,
)
from .tracking_parse import parse_tiktok_logistics
from .zclaw import visit_page

TRACKING_KEY_PATTERN = re.compile(
    r"(?:tracking|waybill|logistic|express|shipment|shipping)",
    re.IGNORECASE,
)
TRACKING_TOKEN_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9._-]{7,}", re.IGNORECASE)


def build_logistics_request(
    order_id: str,
    *,
    fulfill_unit_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """构造物流详情查询的标准字段。"""
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        raise ValueError("empty-order-id")
    return {
        "main_order_id": normalized_order_id,
        "fulfill_unit_ids": [str(value) for value in fulfill_unit_ids if str(value)],
    }


def build_logistics_query(
    order_id: str,
    *,
    fulfill_unit_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """构造前端 `sn` 序列化器使用的 GET 查询参数。"""
    request = build_logistics_request(
        order_id,
        fulfill_unit_ids=fulfill_unit_ids,
    )
    query: dict[str, Any] = {"main_order_id": request["main_order_id"]}
    for index, fulfill_unit_id in enumerate(request["fulfill_unit_ids"]):
        query[f"fulfill_unit_ids[{index}]"] = fulfill_unit_id
    return query


def _iter_tracking_values(value: Any, *, key_hint: str = "") -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            yield from _iter_tracking_values(
                nested_value,
                key_hint=str(key),
            )
        return
    if isinstance(value, list):
        for nested_value in value:
            yield from _iter_tracking_values(nested_value, key_hint=key_hint)
        return
    if TRACKING_KEY_PATTERN.search(key_hint) and isinstance(value, (str, int, float)):
        normalized = str(value).strip()
        if normalized:
            yield normalized


def parse_logistics_payload(
    payload: dict[str, Any],
    *,
    order_id: str,
) -> dict[str, Any]:
    """从包裹详情响应中提取 TikTok 物流单号。"""
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PageApiSchemaError("物流详情响应缺少 data 对象")

    candidates: list[str] = []
    for value in _iter_tracking_values(data):
        candidates.append(value)
        parsed = parse_tiktok_logistics(value, order_id=order_id)
        if parsed.get("tracking_no"):
            return {
                "ok": True,
                "order_id": str(order_id),
                "tracking_raw": parsed.get("tracking_raw") or value,
                "tracking_no": parsed.get("tracking_no") or "",
                "via": "api-field",
            }

        for token in TRACKING_TOKEN_PATTERN.findall(value):
            if token != str(order_id) and len(token) >= 8:
                return {
                    "ok": True,
                    "order_id": str(order_id),
                    "tracking_raw": value,
                    "tracking_no": token,
                    "via": "api-field-token",
                }

    raise PageApiSchemaError(
        f"物流详情未找到运单号 order_id={order_id} candidates={candidates[:8]}"
    )


def fetch_tiktok_tracking_api(
    store_id: str,
    order_id: str,
    *,
    shop_id: str = "",
    shop_region: str = "US",
    fulfill_unit_ids: Iterable[str] = (),
    wait: float = 4.0,
    retries: int = 1,
) -> dict[str, Any]:
    """打开商家订单页后，通过页面同源 API 查询物流单号。"""
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return {"ok": False, "error": "empty-order-id"}

    visit_page(
        store_id,
        order_lookup_url(
            normalized_order_id,
            shop_id=str(shop_id or ""),
            shop_region=str(shop_region or "US"),
        ),
    )
    time.sleep(max(1.0, wait))
    context = get_seller_page_context(
        store_id,
        shop_id=str(shop_id or ""),
        shop_region=str(shop_region or "US"),
    )
    payload = get_seller_read_json(
        store_id,
        SELLER_LOGISTICS_ENDPOINT,
        query=build_logistics_query(
            normalized_order_id,
            fulfill_unit_ids=fulfill_unit_ids,
        ),
        context=context,
        retries=retries,
    )
    return parse_logistics_payload(payload, order_id=normalized_order_id)
