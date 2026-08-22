#!/usr/bin/env python3
"""商家订单页物流查询 API 适配器。"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .order_dom import order_lookup_url
from .page_api import (
    SELLER_LOGISTICS_ENDPOINT,
    PageApiSchemaError,
    get_seller_page_context,
    get_seller_read_json,
)
from .sample_navigation import is_seller_order_href, navigate_to_url
from .tracking_parse import normalize_carrier, normalize_tracking

TRACKING_KEY_PATTERN = re.compile(
    r"(?:tracking|waybill|logistic|express|shipment|shipping)",
    re.IGNORECASE,
)
CARRIER_KEY_PATTERN = re.compile(
    r"(?:carrier|provider|company|courier|shipping_name|logistics_name|provider_name)",
    re.IGNORECASE,
)
TRACKING_TOKEN_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9._-]{7,}", re.IGNORECASE)
SENSITIVE_PAYLOAD_KEY_PATTERN = re.compile(
    r"(?:phone|mobile|address|receiver|token|cookie|secret)",
    re.IGNORECASE,
)

# 仅下列路径允许读取。来源：2 号店已发货 58/58 只读抓包，2026-08-21。
LOGISTICS_PACKAGE_LIST = "package_list"
LOGISTICS_TRACKING_NO = "tracking_no"
LOGISTICS_SUPPLIER = "logistic_supplier"
LOGISTICS_SUPPLIER_NAME = "supplier_name"
LOGISTICS_DETAIL = "logistic_detail"
LOGISTICS_TRACK_LIST = "track_list"
LOGISTICS_TRACK_TITLE = "title"
LOGISTICS_TRACK_TIME = "time"
LOGISTICS_TRACK_CONTENT = "content"
LOGISTICS_ETA_TEXT = "predict_delivery_time_text"
LOGISTICS_FULFILL_UNIT_ID = "fulfill_unit_id"

LOGISTICS_PROGRESS = {
    "unknown": 0,
    "label_created": 1,
    "pending_pickup": 2,
    "in_transit": 3,
    "out_for_delivery": 4,
    "delivered": 5,
}
LOGISTICS_EXCEPTION_CATEGORIES = frozenset({"exception", "returned", "lost"})


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


def _iter_named_values(
    value: Any,
    *,
    key_hint: str = "",
    key_pattern: re.Pattern[str],
) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        sibling_carrier = ""
        for key, nested_value in value.items():
            if CARRIER_KEY_PATTERN.search(str(key)) and isinstance(
                nested_value, (str, int)
            ):
                sibling_carrier = normalize_carrier(str(nested_value))
                if sibling_carrier:
                    break
        for key, nested_value in value.items():
            if key_pattern.search(str(key)) and isinstance(nested_value, (str, int, float)):
                normalized = str(nested_value).strip()
                if normalized:
                    yield normalized, sibling_carrier
            yield from _iter_named_values(
                nested_value,
                key_hint=str(key),
                key_pattern=key_pattern,
            )
        return
    if isinstance(value, list):
        for nested_value in value:
            yield from _iter_named_values(
                nested_value,
                key_hint=key_hint,
                key_pattern=key_pattern,
            )


def parse_logistics_payload(
    payload: dict[str, Any],
    *,
    order_id: str,
) -> dict[str, Any]:
    """从包裹详情响应中提取 TikTok 物流单号和承运商原文。"""
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PageApiSchemaError("物流详情响应缺少 data 对象")

    candidates: list[str] = []
    for value, sibling_carrier in _iter_named_values(
        data,
        key_pattern=TRACKING_KEY_PATTERN,
    ):
        candidates.append(value)
        parsed = normalize_tracking(value, carrier=sibling_carrier)
        if parsed.get("tracking_no") and parsed.get("tracking_no") != str(order_id):
            return {
                "ok": True,
                "order_id": str(order_id),
                "tracking_raw": parsed.get("tracking_raw") or value,
                "tracking_no": parsed.get("tracking_no") or "",
                "via": "api-field",
            }

        for token in TRACKING_TOKEN_PATTERN.findall(value):
            if token != str(order_id) and len(token) >= 8:
                token_parsed = normalize_tracking(token, carrier=sibling_carrier)
                return {
                    "ok": True,
                    "order_id": str(order_id),
                    "tracking_raw": token_parsed.get("tracking_raw") or value,
                    "tracking_no": token_parsed.get("tracking_no") or token,
                    "via": "api-field-token",
                }

    raise PageApiSchemaError(
        f"物流详情未找到运单号 order_id={order_id} candidates={candidates[:8]}"
    )


def _utc_datetime_from_timestamp(value: Any) -> datetime | None:
    normalized_value = str(value or "").strip()
    if not normalized_value.isdecimal():
        return None
    try:
        timestamp = int(normalized_value)
        if timestamp >= 10**12:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _without_sensitive_payload_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_sensitive_payload_keys(nested_value)
            for key, nested_value in value.items()
            if not SENSITIVE_PAYLOAD_KEY_PATTERN.search(str(key))
        }
    if isinstance(value, list):
        return [_without_sensitive_payload_keys(item) for item in value]
    return value


def _logistics_payload_hash(data: dict[str, Any]) -> str:
    sanitized_data = _without_sensitive_payload_keys(data)
    serialized_data = json.dumps(
        sanitized_data,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ).encode()
    return hashlib.sha256(serialized_data).hexdigest()


def _empty_logistics_details(*, data: dict[str, Any], order_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "order_id": str(order_id),
        "tracking_no": "",
        "tracking_raw": "",
        "carrier": "",
        "package_count": 0,
        "status_label": "",
        "status_category": "unknown",
        "estimated_delivery_at": None,
        "delivered_at": None,
        "last_event_text": "",
        "last_event_at": None,
        "fulfill_unit_id": "",
        "needs_delivery_time_confirmation": False,
        "missing_fields": ["tracking_no", "status_label"],
        "raw_payload_hash": _logistics_payload_hash(data),
        "via": "details-no-tracking",
    }


def parse_logistics_details(
    payload: dict[str, Any],
    *,
    order_id: str,
) -> dict[str, Any]:
    """Parse verified logistics detail fields without inferring delivery."""
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PageApiSchemaError("物流详情响应缺少 data 对象")

    package_values = data.get(LOGISTICS_PACKAGE_LIST)
    if not isinstance(package_values, list) or not package_values:
        return _empty_logistics_details(data=data, order_id=order_id)

    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    from assistant.domain.shipment_status import normalize_shipment_status

    parsed_packages: list[dict[str, Any]] = []
    for package_value in package_values:
        package = package_value if isinstance(package_value, dict) else {}
        supplier_value = package.get(LOGISTICS_SUPPLIER)
        supplier = supplier_value if isinstance(supplier_value, dict) else {}
        carrier = normalize_carrier(supplier.get(LOGISTICS_SUPPLIER_NAME))
        tracking = normalize_tracking(
            str(package.get(LOGISTICS_TRACKING_NO) or ""),
            carrier=carrier,
        )

        logistics_detail_value = package.get(LOGISTICS_DETAIL)
        logistics_detail = (
            logistics_detail_value
            if isinstance(logistics_detail_value, dict)
            else {}
        )
        track_list_value = logistics_detail.get(LOGISTICS_TRACK_LIST)
        track_list = track_list_value if isinstance(track_list_value, list) else []
        latest_event_value = track_list[0] if track_list else {}
        latest_event = (
            latest_event_value if isinstance(latest_event_value, dict) else {}
        )
        status_label = str(latest_event.get(LOGISTICS_TRACK_TITLE) or "").strip()
        last_event_at = _utc_datetime_from_timestamp(
            latest_event.get(LOGISTICS_TRACK_TIME)
        )
        normalized_status = normalize_shipment_status(
            status_label=status_label,
            delivered_at=None,
        )
        status_category = str(normalized_status["status_category"])
        delivered_at = last_event_at if status_category == "delivered" else None

        eta_text = str(package.get(LOGISTICS_ETA_TEXT) or "").strip()
        estimated_delivery_at = (
            _utc_datetime_from_timestamp(eta_text) if eta_text.isdecimal() else None
        )
        parsed_packages.append(
            {
                "tracking_no": str(tracking.get("tracking_no") or ""),
                "tracking_raw": str(tracking.get("tracking_raw") or ""),
                "carrier": carrier,
                "status_label": status_label,
                "status_category": status_category,
                "estimated_delivery_at": estimated_delivery_at,
                "delivered_at": delivered_at,
                "last_event_text": str(
                    latest_event.get(LOGISTICS_TRACK_CONTENT) or ""
                ).strip(),
                "last_event_at": last_event_at,
                "fulfill_unit_id": str(
                    package.get(LOGISTICS_FULFILL_UNIT_ID) or ""
                ).strip(),
            }
        )

    package_categories = [
        package["status_category"] for package in parsed_packages
    ]
    has_exception_category = any(
        category in LOGISTICS_EXCEPTION_CATEGORIES for category in package_categories
    )
    if has_exception_category and "delivered" in package_categories:
        status_category = "exception"
        representative_package = next(
            package
            for package in parsed_packages
            if package["status_category"] in LOGISTICS_EXCEPTION_CATEGORIES
        )
    elif all(category == "delivered" for category in package_categories):
        status_category = "delivered"
        representative_package = max(
            parsed_packages,
            key=lambda package: package["delivered_at"]
            or datetime.min.replace(tzinfo=timezone.utc),
        )
    elif has_exception_category:
        representative_package = next(
            package
            for package in parsed_packages
            if package["status_category"] in LOGISTICS_EXCEPTION_CATEGORIES
        )
        status_category = str(representative_package["status_category"])
    else:
        representative_package = min(
            parsed_packages,
            key=lambda package: LOGISTICS_PROGRESS.get(
                package["status_category"],
                LOGISTICS_PROGRESS["unknown"],
            ),
        )
        status_category = str(representative_package["status_category"])

    delivered_timestamps = [
        package["delivered_at"]
        for package in parsed_packages
        if package["delivered_at"] is not None
    ]
    delivered_at = (
        max(delivered_timestamps)
        if status_category == "delivered" and delivered_timestamps
        else None
    )
    tracking_package = next(
        (package for package in parsed_packages if package["tracking_no"]),
        representative_package,
    )
    eta_timestamps = [
        package["estimated_delivery_at"]
        for package in parsed_packages
        if package["estimated_delivery_at"] is not None
    ]
    estimated_delivery_at = max(eta_timestamps) if eta_timestamps else None

    missing_fields: list[str] = []
    if not tracking_package["tracking_no"]:
        missing_fields.append("tracking_no")
    if not representative_package["status_label"]:
        missing_fields.append("status_label")
    if status_category == "delivered" and delivered_at is None:
        missing_fields.append("delivered_at")

    return {
        "ok": True,
        "order_id": str(order_id),
        "tracking_no": tracking_package["tracking_no"],
        "tracking_raw": tracking_package["tracking_raw"],
        "carrier": tracking_package["carrier"],
        "package_count": len(package_values),
        "status_label": representative_package["status_label"],
        "status_category": status_category,
        "estimated_delivery_at": estimated_delivery_at,
        "delivered_at": delivered_at,
        "last_event_text": representative_package["last_event_text"],
        "last_event_at": representative_package["last_event_at"],
        "fulfill_unit_id": representative_package["fulfill_unit_id"],
        "needs_delivery_time_confirmation": (
            status_category == "delivered" and delivered_at is None
        ),
        "missing_fields": missing_fields,
        "raw_payload_hash": _logistics_payload_hash(data),
        "via": "api-field" if tracking_package["tracking_no"] else "details-no-tracking",
    }


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

    navigate_to_url(
        store_id,
        order_lookup_url(
            normalized_order_id,
            shop_id=str(shop_id or ""),
            shop_region=str(shop_region or "US"),
        ),
        is_seller_order_href,
        timeout=max(30.0, wait + 20.0),
        poll_interval=0.5,
    )
    time.sleep(max(0.8, min(wait, 2.5)))
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


def fetch_tiktok_logistics_payload(
    store_id: str,
    order_id: str,
    *,
    shop_id: str = "",
    shop_region: str = "US",
    fulfill_unit_ids: Iterable[str] = (),
    wait: float = 4.0,
    retries: int = 1,
) -> dict[str, Any]:
    """Fetch the raw allowlisted logistics JSON without persisting it."""
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        raise ValueError("empty-order-id")

    navigate_to_url(
        store_id,
        order_lookup_url(
            normalized_order_id,
            shop_id=str(shop_id or ""),
            shop_region=str(shop_region or "US"),
        ),
        is_seller_order_href,
        timeout=max(30.0, wait + 20.0),
        poll_interval=0.5,
    )
    time.sleep(max(0.8, min(wait, 2.5)))
    context = get_seller_page_context(
        store_id,
        shop_id=str(shop_id or ""),
        shop_region=str(shop_region or "US"),
    )
    return get_seller_read_json(
        store_id,
        SELLER_LOGISTICS_ENDPOINT,
        query=build_logistics_query(
            normalized_order_id,
            fulfill_unit_ids=fulfill_unit_ids,
        ),
        context=context,
        retries=retries,
    )


def fetch_tiktok_logistics_details_api(
    store_id: str,
    order_id: str,
    *,
    shop_id: str = "",
    shop_region: str = "US",
    fulfill_unit_ids: Iterable[str] = (),
    wait: float = 4.0,
    retries: int = 1,
) -> dict[str, Any]:
    """Fetch and parse verified logistics detail fields."""
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return {"ok": False, "error": "empty-order-id"}
    payload = fetch_tiktok_logistics_payload(
        store_id,
        normalized_order_id,
        shop_id=shop_id,
        shop_region=shop_region,
        fulfill_unit_ids=fulfill_unit_ids,
        wait=wait,
        retries=retries,
    )
    return parse_logistics_details(payload, order_id=normalized_order_id)
