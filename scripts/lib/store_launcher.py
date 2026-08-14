#!/usr/bin/env python3
"""样品筛查的 GUI + ZClaw 店铺启动编排。"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .zclaw import list_running_stores, open_store, probe_store_page


def ensure_sample_store_open(
    store_id: str,
    *,
    store_name: str = "",
    wait_seconds: float = 2.0,
    open_store_fn: Callable[[str], dict[str, Any]] = open_store,
    list_running_stores_fn: Callable[[], list[dict[str, Any]]] = list_running_stores,
    probe_store_page_fn: Callable[..., dict[str, Any]] = probe_store_page,
) -> dict[str, Any]:
    """确保目标店通过 ZClaw 运行；目标已运行时绝不重开。"""
    normalized_store_id = str(store_id or "").strip()
    if not normalized_store_id:
        raise ValueError("打开店铺必须显式提供 --store-id")

    running_stores = [
        store
        for store in list_running_stores_fn()
        if str(store.get("storeId") or "").strip()
    ]
    target_matches = [
        store
        for store in running_stores
        if str(store.get("storeId") or "").strip() == normalized_store_id
    ]
    other_running_stores = [
        store
        for store in running_stores
        if str(store.get("storeId") or "").strip() != normalized_store_id
    ]

    if other_running_stores:
        running_summary = ", ".join(
            f"{store.get('storeName') or '未知店铺'}({store.get('storeId')})"
            for store in other_running_stores
        )
        raise RuntimeError(
            "当前有其他店铺正在运行，脚本不会自动关闭或切换："
            f"{running_summary}。请先手动关闭后重试。"
        )

    opened_now = False
    open_result: dict[str, Any] = {}
    if not target_matches:
        open_result = open_store_fn(normalized_store_id)
        opened_now = True
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    probe = probe_store_page_fn(normalized_store_id, retries=4)
    return {
        "ok": True,
        "store_id": normalized_store_id,
        "store_name": (
            str(target_matches[0].get("storeName") or "").strip()
            if target_matches
            else str(store_name or "").strip()
        ),
        "opened_now": opened_now,
        "already_running": bool(target_matches),
        "open_result": open_result,
        "probe": probe,
    }
