#!/usr/bin/env python3
"""样品筛查的 GUI + ZClaw 店铺启动编排。"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from .zclaw import (
    close_store,
    list_all_stores,
    list_running_stores,
    open_store,
    probe_store_page,
)

logger = logging.getLogger(__name__)

# 对齐紫鸟客户端 close 后 sleep(2500) 再 launch。
CLOSE_SETTLE_SECONDS = 2.5
CLOSE_WAIT_SECONDS = 30.0
CLOSE_POLL_SECONDS = 1.0
# ZClaw open_store 在内核未下载时可能要数分钟。
OPEN_STORE_TIMEOUT_SECONDS = 300
PROBE_WAIT_SECONDS = 180.0
PROBE_POLL_SECONDS = 3.0
# 仅 0 号：工作台没有打开的店时默认开 2 号店（带 debugPort）。
DEFAULT_PREPARE_STORE_ID = "27506607043054"
DEFAULT_PREPARE_STORE_NAME = "跨境2号店"


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


def _running_with_id(stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [store for store in stores if str(store.get("storeId") or "").strip()]


def _store_id_of(store: dict[str, Any]) -> str:
    return str(store.get("storeId") or "").strip()


def _store_name_of(store: dict[str, Any]) -> str:
    return str(store.get("storeName") or store.get("name") or "").strip()


def _refuse_other_running(
    *,
    target_store_id: str,
    running_stores: list[dict[str, Any]],
) -> None:
    other_running_stores = [
        store
        for store in running_stores
        if _store_id_of(store) != target_store_id
    ]
    if not other_running_stores:
        return
    running_summary = ", ".join(
        f"{_store_name_of(store) or '未知店铺'}({_store_id_of(store)})"
        for store in other_running_stores
    )
    raise RuntimeError(
        "当前有其他店铺正在运行，脚本不会自动关闭或切换："
        f"{running_summary}。请先手动关闭后重试。"
    )


def resolve_store_id_for_prepare(
    *,
    store_id: str | None = None,
    store_name: str | None = None,
    default_store_id: str | None = DEFAULT_PREPARE_STORE_ID,
    list_running_stores_fn: Callable[[], list[dict[str, Any]]] = list_running_stores,
    list_all_stores_fn: Callable[..., list[dict[str, Any]]] = list_all_stores,
) -> str:
    """0 号开店用的 storeId。不回落到测试 1 号店。

    优先级：显式 id → running 精确店名 → running 恰好 1 家 →
    list_stores 精确店名 → 无 running 时默认 2 号店。
    """
    if store_id is not None and str(store_id).strip():
        sid = str(store_id).strip()
        logger.info(f"[店铺] 使用指定 storeId={sid}")
        return sid

    running = _running_with_id(list_running_stores_fn())
    name_q = (store_name or "").strip()

    if name_q:
        hits = [store for store in running if _store_name_of(store) == name_q]
        if len(hits) == 1:
            sid = _store_id_of(hits[0])
            logger.info(f"[店铺] running 精确匹配 storeName={name_q} → {sid}")
            return sid
        if hits:
            raise RuntimeError(
                f"running 中 storeName={name_q!r} 匹配到 {len(hits)} 家，无法唯一解析"
            )
        if running:
            raise RuntimeError(
                f"running 中无精确店名 {name_q!r}；请先关掉其它店或改用 --store-id"
            )

    if len(running) == 1 and not name_q:
        sid = _store_id_of(running[0])
        logger.info(
            f"[店铺] running 唯一店 → {sid} ({_store_name_of(running[0])})"
        )
        return sid
    if len(running) > 1:
        detail = "、".join(
            _store_name_of(store) or _store_id_of(store) for store in running
        )
        raise RuntimeError(f"工作台开了多家店（{detail}）。请关掉其它店，只留一家。")

    listed = _running_with_id(list_all_stores_fn(limit=50))
    if name_q:
        hits = [store for store in listed if _store_name_of(store) == name_q]
        if len(hits) == 1:
            sid = _store_id_of(hits[0])
            logger.info(f"[店铺] list_stores 精确匹配 storeName={name_q} → {sid}")
            return sid
        if not hits:
            raise RuntimeError(f"账号下无精确店名 {name_q!r}；请改用 --store-id")
        raise RuntimeError(
            f"账号下 storeName={name_q!r} 匹配到 {len(hits)} 家，请用 --store-id"
        )

    default_sid = str(default_store_id or "").strip()
    if default_sid:
        if listed:
            default_hits = [
                store for store in listed if _store_id_of(store) == default_sid
            ]
            if not default_hits:
                raise RuntimeError(
                    f"工作台没有打开的店，账号下也找不到默认的 "
                    f"{DEFAULT_PREPARE_STORE_NAME}（{default_sid}）。请传 --store-id。"
                )
        logger.info(
            f"[店铺] 没有打开的店，默认打开 {DEFAULT_PREPARE_STORE_NAME} "
            f"storeId={default_sid}"
        )
        return default_sid

    raise RuntimeError(
        "工作台没有打开的店，也无法唯一确定要开哪一家。"
        "请先在工作台点开要开的那一家，再双击「0-打开店铺」；"
        "或由技术人员传 --store-id。"
    )


def _wait_until_store_closed(
    store_id: str,
    *,
    list_running_stores_fn: Callable[[], list[dict[str, Any]]],
    sleep_fn: Callable[[float], None],
    timeout_seconds: float,
    poll_seconds: float,
    clock_fn: Callable[[], float],
) -> None:
    deadline = clock_fn() + max(0.0, timeout_seconds)
    while True:
        running = [
            store
            for store in _running_with_id(list_running_stores_fn())
            if _store_id_of(store) == store_id
        ]
        if not running:
            return
        if clock_fn() >= deadline:
            raise RuntimeError(
                f"关闭店铺超时（storeId={store_id} 仍在 running）。请手动关掉后重试。"
            )
        sleep_fn(max(0.1, poll_seconds))


def _wait_until_probe_ready(
    store_id: str,
    *,
    probe_store_page_fn: Callable[..., dict[str, Any]],
    sleep_fn: Callable[[float], None],
    timeout_seconds: float,
    poll_seconds: float,
    clock_fn: Callable[[], float],
) -> dict[str, Any]:
    deadline = clock_fn() + max(0.0, timeout_seconds)
    last_error: BaseException | None = None
    while True:
        try:
            probe = probe_store_page_fn(store_id, retries=0)
            if isinstance(probe, dict):
                return probe
            last_error = RuntimeError(f"probe_store_page bad result: {probe!r}"[:300])
        except Exception as error:
            last_error = error
        if clock_fn() >= deadline:
            hint = (
                "店铺窗口可能还在下载内核或刚打开。"
                "请等窗口不再转圈后重试「0-打开店铺」。"
            )
            raise RuntimeError(
                f"打开后 execute_script 探活失败（storeId={store_id}）: {last_error}\n"
                f"提示: {hint}"
            ) from last_error
        logger.info("  [开店] 调试口还没就绪，继续等…")
        sleep_fn(max(0.1, poll_seconds))


def prepare_sample_store_debug(
    store_id: str,
    *,
    store_name: str = "",
    wait_seconds: float = 2.0,
    close_settle_seconds: float = CLOSE_SETTLE_SECONDS,
    close_wait_seconds: float = CLOSE_WAIT_SECONDS,
    probe_wait_seconds: float = PROBE_WAIT_SECONDS,
    open_store_timeout: int = OPEN_STORE_TIMEOUT_SECONDS,
    open_store_fn: Callable[..., dict[str, Any]] = open_store,
    close_store_fn: Callable[..., dict[str, Any]] = close_store,
    list_running_stores_fn: Callable[[], list[dict[str, Any]]] = list_running_stores,
    probe_store_page_fn: Callable[..., dict[str, Any]] = probe_store_page,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """用 ZClaw open_store 打开目标店（带 debugPort）。

    目标已 running 时先 close 再 open（工作台按钮开的店通常没有 debugPort）。
    有其他店 running 时拒绝切店。不走 page extract --mode running。
    """
    normalized_store_id = str(store_id or "").strip()
    if not normalized_store_id:
        raise ValueError("打开店铺必须显式提供 --store-id")

    running_stores = _running_with_id(list_running_stores_fn())
    _refuse_other_running(
        target_store_id=normalized_store_id,
        running_stores=running_stores,
    )
    target_matches = [
        store
        for store in running_stores
        if _store_id_of(store) == normalized_store_id
    ]
    resolved_name = (
        _store_name_of(target_matches[0])
        if target_matches
        else str(store_name or "").strip()
    )

    reopened = False
    close_result: dict[str, Any] = {}
    if target_matches:
        logger.info(
            f"[开店] 目标店已在运行，将关闭后重开（带调试口） "
            f"storeId={normalized_store_id}"
        )
        try:
            close_result = close_store_fn(normalized_store_id)
        except Exception as error:
            still_running = [
                store
                for store in _running_with_id(list_running_stores_fn())
                if _store_id_of(store) == normalized_store_id
            ]
            if still_running:
                raise RuntimeError(f"关闭店铺失败: {error}") from error
            logger.info("[开店] close_store 报错，但 running 里已经没有这家店，继续打开。")
        if close_settle_seconds > 0:
            sleep_fn(close_settle_seconds)
        _wait_until_store_closed(
            normalized_store_id,
            list_running_stores_fn=list_running_stores_fn,
            sleep_fn=sleep_fn,
            timeout_seconds=close_wait_seconds,
            poll_seconds=CLOSE_POLL_SECONDS,
            clock_fn=clock_fn,
        )
        reopened = True

    logger.info(
        f"[开店] 调用 open_store（ZClaw / debugPort） storeId={normalized_store_id}"
    )
    open_result = open_store_fn(
        normalized_store_id,
        timeout=open_store_timeout,
    )
    if open_result.get("kernelDownloading"):
        hint = str(open_result.get("zclawLaunchHint") or "").strip()
        logger.info(
            "[开店] 紫鸟正在下载浏览器内核"
            + (f"：{hint}" if hint else "，可能需要几分钟。")
        )
    if wait_seconds > 0:
        sleep_fn(wait_seconds)

    probe = _wait_until_probe_ready(
        normalized_store_id,
        probe_store_page_fn=probe_store_page_fn,
        sleep_fn=sleep_fn,
        timeout_seconds=probe_wait_seconds,
        poll_seconds=PROBE_POLL_SECONDS,
        clock_fn=clock_fn,
    )
    return {
        "ok": True,
        "store_id": normalized_store_id,
        "store_name": resolved_name,
        "reopened": reopened,
        "opened_now": True,
        "already_running": False,
        "close_result": close_result,
        "open_result": open_result,
        "probe": probe,
    }
