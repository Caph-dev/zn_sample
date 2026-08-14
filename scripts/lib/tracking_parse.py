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
_AFTER_LABEL_RE = re.compile(
    r"TikTok\s*物流[^\n]{0,20}[\n\r\t :：]*"
    r"((?:CBT|USPS|UPS|FedEx|DHL)[,，]?\s*)?"
    r"([A-Z0-9][A-Z0-9]{7,})",
    re.I,
)


def is_order_id(value: str | None) -> bool:
    return bool(_ORDER_ID_RE.fullmatch((value or "").strip()))


def normalize_tracking(raw: str | None) -> dict[str, str]:
    """拆成飞书/私信展示原文，以及用于比对的核心单号。

    9200 开头的 TikTok 物流展示为「CBT, {单号}」。
    """
    text = re.sub(r"\s+", " ", (raw or "").strip())
    text = text.replace("，", ",").strip(" ,")
    if not text:
        return {"tracking_raw": "", "tracking_no": ""}
    # 去掉标签残留
    text = re.sub(r"^TikTok\s*物流[:：\s]*", "", text, flags=re.I).strip()
    carrier = ""
    number = text
    match = re.match(
        r"^(CBT|USPS|UPS|FedEx|DHL)[, ]+(.+)$",
        text,
        flags=re.I,
    )
    if match:
        carrier = match.group(1).upper()
        number = match.group(2).strip(" ,")
    number = re.sub(r"\s+", "", number)
    if is_order_id(number):
        return {"tracking_raw": "", "tracking_no": ""}
    if not carrier and number.upper().startswith("9200"):
        carrier = "CBT"
    if carrier:
        raw_out = f"{carrier}, {number}"
    else:
        raw_out = number
    return {"tracking_raw": raw_out, "tracking_no": number}


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
            f"{labeled.group(1) or ''}{labeled.group(2) or ''}"
        )
        if parsed["tracking_no"] and parsed["tracking_no"] != oid:
            parsed["via"] = "label"
            return parsed

    # 回退：正文里的运单形态 token，排除订单号
    for token in _TRACK_TOKEN_RE.findall(text):
        parsed = normalize_tracking(token)
        if parsed["tracking_no"] and parsed["tracking_no"] != oid:
            # 若 token 前 20 字内有 CBT/USPS，带上承运商
            idx = text.find(token)
            window = text[max(0, idx - 24) : idx]
            prefix = ""
            if re.search(r"\bCBT\b", window, re.I):
                prefix = "CBT, "
            elif re.search(r"\bUSPS\b", window, re.I):
                prefix = "USPS, "
            if prefix and not parsed["tracking_raw"].startswith(("CBT", "USPS")):
                parsed["tracking_raw"] = prefix + parsed["tracking_no"]
            parsed["via"] = "token"
            return parsed

    return {"tracking_raw": "", "tracking_no": "", "via": "miss"}
