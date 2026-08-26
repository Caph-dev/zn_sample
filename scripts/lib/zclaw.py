#!/usr/bin/env python3
"""紫鸟 ZClaw 薄封装（对齐 zn_daren/scripts/lib/zclaw_dom 用法）。"""
from __future__ import annotations

import logging

import json
import subprocess
import time
from typing import Any

from .time_budget import remaining_seconds
from .zclaw_cli import run_ziniao_cli

logger = logging.getLogger(__name__)

# 长跑后 execute_script 偶发 network 假阳性（doctor 仍绿）；批准前会探活+重试
DEFAULT_EXEC_RETRIES = 4
DEFAULT_EXEC_RETRY_BASE_SEC = 1.5
# 跨域卸页/SPA 加载时 execute_script 会卡住。实测订单页 reload 后：
# loading 期单次探测 5.4s，interactive 期 5.6–11.5s，complete 前最后一次 24s。
# 短超时（2s）只会让每次探测都超时、把整个等待预算吃光。href/就绪探测
# 必须给足单次上限；总预算仍由调用方 deadline 控制。
HREF_PROBE_TIMEOUT_SECONDS = 15


def is_timeout_expired_error(error: BaseException) -> bool:
    """是否为 subprocess TimeoutExpired（或同义超时异常）。"""
    if isinstance(error, subprocess.TimeoutExpired):
        return True
    error_type_name = type(error).__name__
    return error_type_name == "TimeoutExpired" or error_type_name.startswith(
        "TimeoutExpired"
    )


def _parse_cli_json_blob(text: str) -> dict | None:
    """从 stdout/stderr 中抽出 CLI JSON（可能夹杂其它行）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def is_bridge_network_error(error: BaseException | str | dict | None) -> bool:
    """是否为「无法连接 Bridge / network」类错误（doctor 仍可能绿）。"""
    if error is None:
        return False
    if isinstance(error, dict):
        nested = error.get("error") if isinstance(error.get("error"), dict) else error
        if isinstance(nested, dict) and str(nested.get("type") or "").lower() == "network":
            return True
        text = json.dumps(error, ensure_ascii=False)
    else:
        text = str(error)
    lowered = text.lower()
    if "无法连接紫鸟浏览器" in text:
        return True
    if "127.0.0.1:9481" in text:
        return True
    if '"type": "network"' in text or '"type":"network"' in lowered:
        return True
    if "bridge" in lowered and "network" in lowered:
        return True
    return False


def zclaw_invoke(tool: str, arguments: dict | None = None, *, timeout: int = 120) -> dict:
    args = json.dumps(arguments or {}, ensure_ascii=False)
    proc = run_ziniao_cli(
        ["zclaw", "invoke", tool, "--args", args],
        timeout=timeout,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    # CLI 成功时常只写 stdout；失败时可能只写 stderr（含 ok:false + network）
    parsed = _parse_cli_json_blob(stdout) or _parse_cli_json_blob(stderr)
    if parsed is not None:
        return parsed
    if not stdout and not stderr:
        raise RuntimeError(f"zclaw {tool} empty output (exit={proc.returncode})")
    raise RuntimeError(
        f"zclaw {tool} bad/empty json (exit={proc.returncode}): "
        f"stdout={(stdout or '')[:400]!r} stderr={(stderr or '')[:400]!r}"
    )


def zclaw_exec(
    store_id: str,
    script: str,
    timeout: int = 120,
    *,
    retries: int = 0,
    retry_base_sec: float = DEFAULT_EXEC_RETRY_BASE_SEC,
    retry_timeout_expired: bool = False,
    deadline: float | None = None,
    operation: str = "execute_script",
) -> Any:
    """执行页面脚本。

    retries>0 时，对 Bridge network 类失败做有限次退避重试
    （长跑详情后批准首包偶发；doctor 绿也不代表 execute 通道稳）。

    ``retry_timeout_expired=True`` 才允许对 TimeoutExpired 重试：
    只读探测 / 物流 GET 等幂等读取可显式开启；批准、发信等写路径
    默认关闭，避免不确定状态下重复执行副作用动作。

    所有重试服从真实墙钟 deadline；剩余时间不足时不再重试。
    """
    attempts = max(1, int(retries) + 1)
    last_error: BaseException | None = None
    started_at = time.monotonic()
    for attempt in range(attempts):
        if deadline is not None:
            remaining = remaining_seconds(deadline)
            if remaining <= 0:
                last_error = subprocess.TimeoutExpired(
                    f"{operation} (deadline)", remaining
                )
                break
            if remaining < 0.5:
                # 剩余时间不足以完成最小 subprocess 调用。
                break
            effective_timeout = max(1, int(min(timeout, remaining)))
        else:
            effective_timeout = int(timeout)
        try:
            outer = zclaw_invoke(
                "execute_script",
                {"storeId": store_id, "script": script},
                timeout=effective_timeout,
            )
            if not outer.get("ok"):
                err = RuntimeError(f"zclaw failed: {outer}")
                if attempt + 1 < attempts and is_bridge_network_error(outer):
                    last_error = err
                    sleep_sec = retry_base_sec * (attempt + 1)
                    if not _retry_backoff(
                        deadline, sleep_sec, operation, attempt, attempts, timeout,
                        "BridgeNetworkError", started_at,
                    ):
                        break
                    continue
                raise err
            result = outer["data"]["data"]["result"]
            if isinstance(result, str):
                try:
                    return json.loads(result)
                except json.JSONDecodeError:
                    return result
            if isinstance(result, dict) and "value" in result:
                val = result["value"]
                if isinstance(val, str):
                    try:
                        return json.loads(val)
                    except json.JSONDecodeError:
                        return val
                return val
            raise RuntimeError(f"unhandled execute_script result: {str(result)[:400]}")
        except Exception as error:
            last_error = error
            retryable = is_bridge_network_error(error) or (
                retry_timeout_expired and is_timeout_expired_error(error)
            )
            if attempt + 1 < attempts and retryable:
                sleep_sec = retry_base_sec * (attempt + 1)
                if not _retry_backoff(
                    deadline, sleep_sec, operation, attempt, attempts, timeout,
                    type(error).__name__, started_at,
                ):
                    break
                continue
            raise
    assert last_error is not None
    raise last_error


def _retry_backoff(
    deadline: float | None,
    sleep_sec: float,
    operation: str,
    attempt: int,
    attempts: int,
    timeout_seconds: int,
    error_type: str,
    started_at: float,
) -> bool:
    """记录结构化重试事件并按需退避；deadline 不足时返回 False。"""
    if deadline is not None:
        remaining = remaining_seconds(deadline)
        if remaining <= sleep_sec:
            return False
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    logger.info(
        "event=zclaw_execute_retry operation=%s attempt=%d max_attempts=%d "
        "timeout_seconds=%d backoff_seconds=%.2f error_type=%s elapsed_ms=%d",
        operation,
        attempt + 1,
        attempts,
        timeout_seconds,
        sleep_sec,
        error_type,
        elapsed_ms,
    )
    time.sleep(sleep_sec)
    return True


def probe_store_page(
    store_id: str,
    *,
    retries: int = DEFAULT_EXEC_RETRIES,
    retry_base_sec: float = DEFAULT_EXEC_RETRY_BASE_SEC,
    timeout: int = 60,
) -> dict[str, Any]:
    """轻量探活：确认 execute_script 通道可用（比 doctor 更接近真实批准）。"""
    script = (
        "(() => JSON.stringify({"
        "href: (location.href||'').slice(0,160),"
        "ready: document.readyState,"
        "title: (document.title||'').slice(0,80),"
        "tr: document.querySelectorAll('table tbody tr').length"
        "}))()"
    )
    result = zclaw_exec(
        store_id,
        script,
        timeout=timeout,
        retries=retries,
        retry_base_sec=retry_base_sec,
    )
    if not isinstance(result, dict):
        raise RuntimeError(f"probe_store_page bad result: {result!r}"[:300])
    return result


def ensure_store_exec_ready(
    store_id: str,
    *,
    label: str = "操作",
    retries: int = DEFAULT_EXEC_RETRIES,
) -> dict[str, Any]:
    """批准/写前探活；失败时给出 doctor vs execute 通道差异说明。"""
    try:
        probe = probe_store_page(store_id, retries=retries)
        href = str(probe.get("href") or "")
        logger.info(
            f"[Bridge探活/{label}] ok href={href[:90]!r} "
            f"tr={probe.get('tr')} ready={probe.get('ready')}")
        return probe
    except Exception as error:
        hint = (
            "doctor 只检测 Bridge HTTP(9481)，与 execute_script 店铺页通道不是同一层；"
            "长跑详情后批准首包常见 network 假阳性。"
            "请确认：1) 紫鸟 GUI 仍开且店铺窗口未关；2) 页在「待审核」列表；"
            "3) 必要时重启紫鸟后重开店，再短跑 --max-rows 试批。"
        )
        raise RuntimeError(
            f"批准前 execute_script 探活失败（storeId={store_id}）: {error}\n提示: {hint}"
        ) from error


def list_running_stores() -> list[dict]:
    outer = zclaw_invoke("extract_data", {"mode": "running"})
    if not outer.get("ok"):
        raise RuntimeError(f"list_running_stores failed: {outer.get('error') or outer}")
    data = outer.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else None
    if items is None and isinstance(data, list):
        items = data
    return list(items or [])


def list_all_stores(limit: int = 50) -> list[dict]:
    """list_stores → [{storeId, storeName, ...}, ...]。"""
    outer = zclaw_invoke("list_stores", {"limit": limit})
    if not outer.get("ok"):
        raise RuntimeError(f"list_stores failed: {outer.get('error') or outer}")
    data = outer.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else None
    if items is None and isinstance(data, list):
        items = data
    return list(items or [])


def open_store(store_id: str, *, timeout: int = 180) -> dict:
    outer = zclaw_invoke("open_store", {"storeId": store_id}, timeout=timeout)
    if not outer.get("ok"):
        raise RuntimeError(f"open_store failed: {outer.get('error') or outer}")
    return outer.get("data") or {}


def close_store(store_id: str, *, timeout: int = 120) -> dict:
    outer = zclaw_invoke("close_store", {"storeId": store_id}, timeout=timeout)
    if not outer.get("ok"):
        raise RuntimeError(f"close_store failed: {outer.get('error') or outer}")
    return outer.get("data") or {}


def visit_page(store_id: str, url: str) -> dict:
    outer = zclaw_invoke("visit_page", {"storeId": store_id, "url": url}, timeout=180)
    # Bridge 偶发抖动：失败时不立刻抛，由调用方校验 href
    return outer


def resolve_store_id(
    *,
    store_id: str | None = None,
    store_name: str | None = None,
    default_store_id: str | None = None,
) -> str:
    """解析 storeId。

    优先级：
      1) 显式 --store-id
      2) --store-name 在 running 中精确唯一匹配
      3) running 恰好 1 家
      4) default_store_id（测试默认 1 号店）且该店在 list 或 running 中可识别时可用
    """
    if store_id is not None and str(store_id).strip():
        sid = str(store_id).strip()
        logger.info(f"[店铺] 使用指定 storeId={sid}")
        return sid

    running = [r for r in list_running_stores() if str(r.get("storeId") or "").strip()]
    name_q = (store_name or "").strip()

    if name_q:
        hits = [r for r in running if str(r.get("storeName") or "").strip() == name_q]
        if len(hits) == 1:
            sid = str(hits[0]["storeId"]).strip()
            logger.info(f"[店铺] running 精确匹配 storeName={name_q} → {sid}")
            return sid
        raise RuntimeError(
            f"running 中 storeName={name_q!r} 匹配到 {len(hits)} 家，无法唯一解析"
        )

    if len(running) == 1:
        sid = str(running[0]["storeId"]).strip()
        logger.info(f"[店铺] running 唯一店 → {sid} ({running[0].get('storeName')})")
        return sid

    if default_store_id and str(default_store_id).strip():
        sid = str(default_store_id).strip()
        logger.info(f"[店铺] 使用测试默认 storeId={sid}")
        return sid

    raise RuntimeError(
        f"无法解析 storeId：running={len(running)} 家；请传 --store-id 或先只开一家店"
    )
