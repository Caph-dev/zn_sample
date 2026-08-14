#!/usr/bin/env python3
"""从商家中心订单页正文抽取 TikTok 物流单号（不是订单 ID）。"""
from __future__ import annotations

import re
from typing import Any

# 订单号（TikTok main_order_id）约 18 位数字，禁止当成运单
_ORDER_ID_RE = re.compile(r"^\d{15,20}$")
_TRACK_TOKEN_RE = re.compile(
    r"(?:"
    r"UUS[A-Z0-9]{10,}"
    r"|GFUS[A-Z0-9]{8,}"
    r"|1LSD[A-Z0-9]{6,}"
    r"|9200\d{16,}"
    r"|[A-Z]{2,4}\d[A-Z0-9]{8,}"
    r")",
    re.I,
)
_CARRIER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .&-]{0,19}$")
_CARRIER_PREFIX_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9 .&-]{0,19})[,，]\s*(.+)$"
)
_AFTER_LABEL_RE = re.compile(
    r"TikTok\s*物流[:：\s]*"
    r"(?:([A-Za-z][A-Za-z0-9.&-]{1,19})[,，]\s*)?"
    r"("
    r"UUS[A-Z0-9]{10,}"
    r"|GFUS[A-Z0-9]{8,}"
    r"|1LSD[A-Z0-9]{6,}"
    r"|9200\d{16,}"
    r"|[A-Z]{2,4}\d[A-Z0-9]{8,}"
    r")",
    re.I,
)
_NEARBY_CARRIER_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9.&-]{1,19})\b[,，]?\s*$"
)


def is_order_id(value: str | None) -> bool:
    return bool(_ORDER_ID_RE.fullmatch((value or "").strip()))


def normalize_carrier(value: str | None) -> str:
    """保留页面/接口给出的承运商原文，不做单号猜测。"""
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = text.replace("，", ",").strip(" ,")
    text = re.sub(r"^TikTok\s*物流[:：\s]*", "", text, flags=re.I).strip()
    if not text or not _CARRIER_NAME_RE.fullmatch(text):
        return ""
    if is_order_id(text) or _TRACK_TOKEN_RE.fullmatch(text):
        return ""
    return text


def format_tracking_display(number: str, *, carrier: str = "") -> str:
    cleaned_number = re.sub(r"\s+", "", (number or "").strip())
    cleaned_carrier = normalize_carrier(carrier)
    if cleaned_carrier and cleaned_number:
        return f"{cleaned_carrier}, {cleaned_number}"
    return cleaned_number


def normalize_tracking(raw: str | None, *, carrier: str = "") -> dict[str, str]:
    """拆成飞书/私信展示原文，以及用于比对的核心单号。

    展示格式为「承运商, 单号」；承运商只采用页面或接口原文，
    不按单号形态猜测 CBT / USPS 等前缀。
    """
    text = re.sub(r"\s+", " ", (raw or "").strip())
    text = text.replace("，", ",").strip(" ,")
    if not text:
        return {"tracking_raw": "", "tracking_no": ""}
    text = re.sub(r"^TikTok\s*物流[:：\s]*", "", text, flags=re.I).strip()
    parsed_carrier = ""
    number = text
    match = _CARRIER_PREFIX_RE.match(text)
    if match:
        parsed_carrier = normalize_carrier(match.group(1))
        number = match.group(2).strip(" ,")
    number = re.sub(r"\s+", "", number)
    if is_order_id(number):
        return {"tracking_raw": "", "tracking_no": ""}
    display_carrier = parsed_carrier or normalize_carrier(carrier)
    return {
        "tracking_raw": format_tracking_display(number, carrier=display_carrier),
        "tracking_no": number,
    }


def tracking_core_number(value: str | None) -> str:
    return str(normalize_tracking(value).get("tracking_no") or "").strip()


def tracking_numbers_equivalent(left: str | None, right: str | None) -> bool:
    """「CBT, 9200…」与纯「9200…」视为同一物流单号。"""
    left_number = tracking_core_number(left)
    right_number = tracking_core_number(right)
    return bool(left_number) and left_number == right_number


def parse_tiktok_logistics(page_text: str, *, order_id: str = "") -> dict[str, Any]:
    """从订单页 innerText 抽 TikTok 物流单号。"""
    text = (page_text or "").replace("\u00a0", " ")
    oid = (order_id or "").strip()

    labeled = _AFTER_LABEL_RE.search(text)
    if labeled:
        parsed = normalize_tracking(
            labeled.group(2) or "",
            carrier=labeled.group(1) or "",
        )
        if parsed["tracking_no"] and parsed["tracking_no"] != oid:
            parsed["via"] = "label"
            return parsed

    # 回退：正文里的运单形态 token，排除订单号
    for token in _TRACK_TOKEN_RE.findall(text):
        parsed = normalize_tracking(token)
        if parsed["tracking_no"] and parsed["tracking_no"] != oid:
            idx = text.find(token)
            window = text[max(0, idx - 32) : idx]
            nearby = _NEARBY_CARRIER_RE.search(window)
            if nearby:
                parsed = normalize_tracking(
                    parsed["tracking_no"],
                    carrier=nearby.group(1),
                )
            parsed["via"] = "token"
            return parsed

    return {"tracking_raw": "", "tracking_no": "", "via": "miss"}
