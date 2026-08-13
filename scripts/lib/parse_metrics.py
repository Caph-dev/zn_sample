#!/usr/bin/env python3
"""金额 / 粉丝 / 百分比解析。"""
from __future__ import annotations

import re
from typing import Any


def parse_money(v: Any) -> float | None:
    """'$14,368.68' / '1.3K' / '2.1k' / 数字 → float USD。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace(" ", "").replace("$", "").replace("USD", "")
    if not s or s in {"-", "--", "N/A", "n/a"}:
        return None
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([kKmMbB万]?)", s)
    if not m:
        # 再试宽松
        m2 = re.search(r"([+-]?\d+(?:\.\d+)?)([kKmMbB万]?)", s)
        if not m2:
            return None
        num, unit = float(m2.group(1)), m2.group(2)
    else:
        num, unit = float(m.group(1)), m.group(2)
    if unit in {"k", "K"}:
        num *= 1_000
    elif unit in {"m", "M"}:
        num *= 1_000_000
    elif unit in {"b", "B"}:
        num *= 1_000_000_000
    elif unit == "万":
        num *= 10_000
    return num


def parse_count(v: Any) -> float | None:
    """粉丝/件数：'26901' / '2.7万' / '1.4 万'。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("\xa0", "").replace(" ", "")
    if not s or s in {"-", "--"}:
        return None
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([kKmMbB万]?)", s)
    if not m:
        m2 = re.search(r"([+-]?\d+(?:\.\d+)?)([kKmMbB万]?)", s)
        if not m2:
            return None
        num, unit = float(m2.group(1)), m2.group(2)
    else:
        num, unit = float(m.group(1)), m.group(2)
    if unit in {"k", "K"}:
        num *= 1_000
    elif unit in {"m", "M"}:
        num *= 1_000_000
    elif unit == "万":
        num *= 10_000
    return num


def parse_percent(v: Any) -> float | None:
    """'97.64%' / 0.9764 / 97.64 → 百分数 0-100。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        return x * 100 if 0 <= x <= 1 else x
    s = str(v).strip().replace("%", "").replace(",", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return x * 100 if 0 <= x <= 1 else x


def female_ratio(gender_list: Any) -> float | None:
    """top_follower_gender: [{key:Female,value:0.56},...] → 0-100 女性占比。"""
    if not gender_list:
        return None
    if isinstance(gender_list, dict):
        gender_list = [gender_list]
    for g in gender_list:
        if not isinstance(g, dict):
            continue
        key = str(g.get("key") or "")
        if re.search(r"female|女", key, re.I):
            try:
                val = float(g.get("value"))
            except (TypeError, ValueError):
                return None
            return val * 100 if 0 <= val <= 1 else val
    return None


def category_names(cats: Any) -> list[str]:
    if not cats:
        return []
    out: list[str] = []
    for c in cats:
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, dict):
            name = c.get("name") or c.get("value") or c.get("key")
            if name:
                out.append(str(name))
    return out
