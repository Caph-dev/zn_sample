#!/usr/bin/env python3
"""样品申请筛查 — 对齐 样品申请筛查sop。

默认：只读导出，**不**点同意。

危险路径（须显式）：
  --execute --yes [--execute-limit N]
  顺序：筛查通过 → 点「同意」成功 →（可选）写飞书「达人关系管理(新)」
  测试环境默认 **不写飞书**；须再加 --write-feishu

硬性纪律：
  - 默认禁止同意；仅 --execute --yes 可批
  - 用户须 **已手动** 打开：样品申请 → 待审核
  - 测试环境默认 **1 号店** storeId=27437742526069
  - 主推表仅飞书 wiki；达人关系表 bitable 见 [feishu.bitable]

示例：
  # 只读筛查
  python3 scripts/screen_sample_requests.py --with-detail --require-detail

  # 用已有筛查导出批准 1 条（不再扫表/拉详情）
  python3 scripts/screen_sample_requests.py \\
    --from-export exports/sample_screen_<时间戳>.json --execute --yes

  # 批 + 写飞书（生产/明确要求时）
  python3 scripts/screen_sample_requests.py \\
    --from-export exports/sample_screen_<时间戳>.json \\
    --execute --yes --write-feishu --execute-limit 1
"""
from __future__ import annotations

import logging

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.approve_dom import click_approve_for_apply_id  # noqa: E402
from lib.app_log import configure_logging  # noqa: E402
from lib.console import is_verbose, set_verbose  # noqa: E402
from lib.export_util import resolve_from_export_arg, write_reports  # noqa: E402
from lib.run_summary import format_job_summary  # noqa: E402
from lib.feishu_bitable import (  # noqa: E402
    DEFAULT_APP_TOKEN,
    DEFAULT_TABLE_ID,
    DEFAULT_VIEW_ID,
    FeishuBitableError,
    build_product_id_to_sku_map,
    create_creator_relation_record,
    find_duplicate_record,
    format_followers_raw,
    format_fulfillment_raw,
    get_bitable_access_token,
    list_sample_product_options,
    resolve_sample_product_for_row,
)
from lib.feishu_hero import (  # noqa: E402
    DEFAULT_APP_ID,
    DEFAULT_HERO_URL,
    DEFAULT_SHEET_TITLE,
    FeishuHeroError,
    load_hero_from_feishu,
    match_hero,
)
from lib.filters import Criteria, evaluate_row  # noqa: E402
from lib.network_observer import (  # noqa: E402
    arm_network_observer,
    drain_network_observer,
)
from lib.sample_navigation import navigate_from_seller_home_to_pending  # noqa: E402
from lib.sample_api import check_pending_application_api  # noqa: E402
from lib.sample_data_source import (  # noqa: E402
    DATA_SOURCE_CHOICES,
    load_creator_detail,
    load_pending_rows,
)
from lib.sample_write_api import (  # noqa: E402
    approve_application_api,
    confirm_application_approved_api,
)
from lib.zclaw import ensure_store_exec_ready, resolve_store_id  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_TEST_STORE_ID = "27437742526069"
DEFAULT_TEST_STORE_NAME = "跨境1号店（Lingerie Outlet）"
DEFAULT_EXECUTE_LIMIT = 1
PLATFORM_STATUS_LAG_MINUTES = 10
ALREADY_EXECUTED_APPROVE_STATUSES = {"approved", "unknown"}


def _load_export_rows(path: Path) -> list[dict[str, Any]]:
    """读取筛查导出 json（数组或 {rows/items}）。"""
    export_path = Path(path)
    data = json.loads(export_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("rows") or data.get("items") or []
    if not isinstance(data, list):
        raise RuntimeError(f"导出不是数组: {export_path}")
    return [row for row in data if isinstance(row, dict)]


def _reject_if_not_exact_hero(
    row: dict[str, Any],
    hero_data: dict[str, Any] | None,
) -> str | None:
    """hero_keys 有值时必须精确命中主推货号/商品 ID；空表交给后续解析拦截。"""
    keys = set((hero_data or {}).get("hero_keys") or set())
    if not keys:
        return None
    matched, reason = match_hero(row, keys)
    if matched:
        return None
    return f"非主推款({reason})"


def _select_execute_candidates_from_export(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从已有筛查结果里挑可批准行；已批准或状态未知的不再自动重试。"""
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("eligible"):
            continue
        if not str(row.get("apply_id") or "").strip():
            continue
        approve_status = str(row.get("approve_status") or "").strip()
        if approve_status in ALREADY_EXECUTED_APPROVE_STATUSES:
            continue
        candidates.append(row)
    return candidates


def _parse_approved_at(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _looks_like_already_approved_skip(row: dict[str, Any]) -> bool:
    """上一轮已批后，下一轮预检常会把同一申请标成「待审核找不到」。"""
    if str(row.get("approve_status") or "").strip() != "skipped":
        return False
    if str(row.get("action") or "").strip() != "skipped-api-preflight-state":
        return False
    approve_error = str(row.get("approve_error") or "")
    return "not-found-in-pending" in approve_error


def _select_confirm_candidates_from_export(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """挑出已批但列表确认仍待延后的行；默认等满平台约 10 分钟刷新窗口。"""
    current_time = now or datetime.now()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        apply_id = str(row.get("apply_id") or "").strip()
        if not apply_id:
            continue
        approve_status = str(row.get("approve_status") or "").strip()
        confirmation = str(row.get("approve_confirmation") or "").strip()
        if approve_status not in {"approved", "unknown"} and not _looks_like_already_approved_skip(row):
            continue
        if confirmation == "confirmed":
            continue
        if str(row.get("feishu_status") or "").strip() in {"created", "duplicate"}:
            continue
        approved_at = _parse_approved_at(row.get("approved_at"))
        if (
            not force
            and approved_at is not None
            and (current_time - approved_at).total_seconds()
            < PLATFORM_STATUS_LAG_MINUTES * 60
        ):
            row["approve_confirmation"] = "waiting-platform-lag"
            continue
        candidates.append(row)
    return candidates


def decide_api_approval_outcome(
    *,
    approve_result: dict[str, Any],
    approve_exception: Exception | None = None,
    pending_recheck: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据批准 API 写结果判定，不等待「待发货」列表刷新。

    平台联盟中心列表大约 10 分钟后才会从待审核转到待发货。
    写接口 ``success_count=1`` 即视为批准成功；只有写失败且仍待审核
    才判失败。超时/身份不一致等才标 unknown。
    """
    write_accepted = bool(approve_result.get("ok")) and approve_exception is None
    if write_accepted:
        return {
            "approve_status": "approved",
            "action": "approved",
            "approve_confirmation": "deferred",
            "stop_round": False,
        }

    pending_state = str((pending_recheck or {}).get("state") or "")
    if pending_state == "pending-approvable":
        return {
            "approve_status": "failed",
            "action": "approve-failed",
            "approve_confirmation": "still-pending",
            "stop_round": True,
        }
    if pending_state == "identity-mismatch":
        return {
            "approve_status": "unknown",
            "action": "approve-unknown",
            "approve_confirmation": "identity-mismatch",
            "stop_round": True,
        }
    return {
        "approve_status": "unknown",
        "action": "approve-unknown",
        "approve_confirmation": pending_state or "write-uncertain",
        "stop_round": True,
    }


def _write_backup(rows: list[dict[str, Any]], prefix: Path) -> dict[str, Path]:
    """批准前备份：json 快照 + 标准导出三件套。"""
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path = prefix.with_name(prefix.name + "_pre_execute.json")
    snapshot_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths = write_reports(rows, prefix.with_name(prefix.name + "_pre_execute"))
    paths["snapshot"] = snapshot_path
    return paths


def _run_execute_pipeline(
    *,
    store_id: str,
    candidates: list[dict[str, Any]],
    hero_data: dict[str, Any] | None,
    write_feishu: bool,
    execute_limit: int,
    execute_delay: float,
    config_path: str | None,
    page_wait: float,
    observe_approve_network: bool,
    write_source: str = "api",
) -> list[dict[str, Any]]:
    """对筛查通过行执行：解析货号 →（可选查重）→ 同意 →（可选写飞书）。"""
    product_id_to_sku = build_product_id_to_sku_map(hero_data or {})
    sample_options: list[str] = []
    bitable_token: str | None = None
    app_token = DEFAULT_APP_TOKEN
    table_id = DEFAULT_TABLE_ID

    if write_feishu:
        try:
            from lib.app_config import load_bitable_settings

            bitable_settings = load_bitable_settings(
                config_path=config_path,
                default_app_token=DEFAULT_APP_TOKEN,
                default_table_id=DEFAULT_TABLE_ID,
                default_view_id=DEFAULT_VIEW_ID,
            )
            app_token = bitable_settings.get("app_token") or DEFAULT_APP_TOKEN
            table_id = bitable_settings.get("table_id") or DEFAULT_TABLE_ID
            bitable_token = get_bitable_access_token(config_path=config_path)
            sample_options = list_sample_product_options(
                bitable_token,
                app_token=app_token,
                table_id=table_id,
            )
            logger.info(
                f"[飞书] 写入开启 table={table_id} 寄样产品选项={len(sample_options)} 个")
        except (FeishuBitableError, Exception) as error:
            logger.error(f"[飞书] 初始化失败，将只批准不写表: {error}")
            write_feishu = False
    else:
        # 不写飞书时仍用主推表解析货号，便于跳过「无货号/无选项」
        sample_options = []
        logger.info("[飞书] 未开启 --write-feishu：批准后不写多维表格")

    # 无 write 时也尽量解析 sku（用 hero 映射）；选项匹配在无 options 时跳过「选项缺失」卡点
    if not sample_options and product_id_to_sku:
        # 仅用于 dry 字段；真正写入前仍须 options
        pass

    limit = execute_limit if execute_limit > 0 else DEFAULT_EXECUTE_LIMIT
    processed = 0
    feishu_created_ids: list[str] = []

    for row in candidates:
        if processed >= limit:
            row["action"] = "skipped-limit"
            row["approve_status"] = "skipped"
            row["approve_error"] = f"超过 --execute-limit {limit}"
            continue

        apply_id = str(row.get("apply_id") or "").strip()
        creator_name = str(row.get("creator_name") or "").strip()
        row["action"] = "execute-pending"
        row["approve_forbidden"] = False
        row["approve_write_source"] = write_source

        if not apply_id:
            row["approve_status"] = "skipped"
            row["approve_error"] = "无 apply_id"
            row["action"] = "skipped-no-apply-id"
            continue
        if not creator_name:
            row["approve_status"] = "skipped"
            row["approve_error"] = "无达人名(creator_name)"
            row["action"] = "skipped-no-creator"
            continue
        if row.get("can_be_approved") is False:
            row["approve_status"] = "skipped"
            row["approve_error"] = "can_be_approved=false"
            row["action"] = "skipped-cannot-approve"
            continue

        not_hero = _reject_if_not_exact_hero(row, hero_data)
        if not_hero:
            row["approve_status"] = "skipped"
            row["feishu_status"] = "skipped"
            row["approve_error"] = not_hero
            row["feishu_error"] = not_hero
            row["action"] = "skipped-not-hero"
            logger.info(f"  [跳过] {creator_name} apply={apply_id} 原因={not_hero}")
            continue

        product_resolve = resolve_sample_product_for_row(
            row,
            product_id_to_sku=product_id_to_sku,
            sample_product_options=sample_options
            or list(product_id_to_sku.values()),  # 无选项表时先用货号本身做「是否有货号」检查
        )
        # 若有真实 options，再严格匹配一次
        if sample_options:
            product_resolve = resolve_sample_product_for_row(
                row,
                product_id_to_sku=product_id_to_sku,
                sample_product_options=sample_options,
            )
        elif product_resolve.get("ok"):
            # 无 options 且不写飞书：允许只批准；标记 sku
            product_resolve = {
                "ok": True,
                "sku": product_resolve.get("sku"),
                "option": product_resolve.get("sku"),
                "reason": "no-options-skip-option-check",
            }

        row["resolved_sku"] = product_resolve.get("sku")
        row["sample_product_option"] = product_resolve.get("option")

        if not product_resolve.get("ok"):
            # 整单跳过：不批不写
            row["approve_status"] = "skipped"
            row["feishu_status"] = "skipped"
            row["approve_error"] = product_resolve.get("reason")
            row["feishu_error"] = product_resolve.get("reason")
            row["action"] = "skipped-product-unresolved"
            logger.info(
                f"  [跳过] {creator_name} apply={apply_id} "
                f"原因={product_resolve.get('reason')}")
            continue

        # 去重（仅写飞书时强制；不写时也可选查重——默认不查以免无 token）
        if write_feishu and bitable_token:
            try:
                dup = find_duplicate_record(
                    bitable_token,
                    creator_handle=creator_name,
                    sample_product=str(product_resolve.get("option")),
                    app_token=app_token,
                    table_id=table_id,
                )
            except FeishuBitableError as error:
                row["approve_status"] = "skipped"
                row["feishu_status"] = "error"
                row["feishu_error"] = f"查重失败: {error}"
                row["action"] = "skipped-dedupe-error"
                logger.info(f"  [跳过] 查重失败 {creator_name}: {error}")
                continue
            if dup:
                row["approve_status"] = "skipped"
                row["feishu_status"] = "duplicate"
                row["feishu_record_id"] = dup.get("record_id")
                row["approve_error"] = "飞书已存在 红人ID+寄样产品"
                row["action"] = "skipped-duplicate"
                logger.info(
                    f"  [跳过-去重] {creator_name} × {product_resolve.get('option')} "
                    f"record={dup.get('record_id')}")
                continue

        try:
            pending_status = check_pending_application_api(
                store_id,
                apply_id,
                expected_creator_id=str(row.get("creator_id") or ""),
                expected_product_id=str(row.get("product_id") or ""),
            )
        except Exception as error:
            row["approve_status"] = "skipped"
            row["approve_error"] = f"批准前只读状态预检失败: {error}"
            row["action"] = "skipped-api-preflight-error"
            logger.info(f"  [跳过] {creator_name} 批准前状态预检失败: {error}")
            continue

        row["approve_preflight"] = pending_status
        if not pending_status.get("ok") or pending_status.get("state") != "pending-approvable":
            row["approve_status"] = "skipped"
            row["approve_error"] = (
                "批准前状态不再可批准: "
                f"{pending_status.get('state') or 'unknown'}"
            )
            row["action"] = "skipped-api-preflight-state"
            logger.info(
                f"  [跳过] {creator_name} 批准前状态="
                f"{pending_status.get('state') or 'unknown'}")
            continue

        logger.info(
            f"  [批准] ({processed + 1}/{limit}) {creator_name} "
            f"apply={apply_id} sku={product_resolve.get('sku')} "
            f"option={product_resolve.get('option')}")
        observer_armed = False
        approval_attempt_started = False
        approve_exception: Exception | None = None
        approve_result: dict[str, Any] = {}
        try:
            # doctor 绿 ≠ execute_script 可用；批准前先探活（含 network 重试）
            ensure_store_exec_ready(store_id, label="批准前")
            approval_attempt_started = True
            if write_source == "api":
                approve_result = approve_application_api(
                    store_id,
                    apply_id,
                    expected_creator_id=str(row.get("creator_id") or ""),
                    expected_product_id=str(row.get("product_id") or ""),
                    preflight_status=pending_status,
                )
                if not approve_result.get("ok"):
                    logger.info(
                        f"    批准 API 未接受请求: {approve_result.get('state')}")
            else:
                if observe_approve_network:
                    arm_network_observer(store_id)
                    observer_armed = True
                click_result = click_approve_for_apply_id(
                    store_id,
                    apply_id,
                    page_wait=max(1.0, page_wait),
                )
                approve_result = click_result
        except Exception as error:
            approve_exception = error
            row["approve_status"] = "error"
            row["approve_error"] = str(error)
            row["action"] = "approve-error"
            logger.info(f"    批准异常: {error}")
        finally:
            if observer_armed:
                try:
                    time.sleep(0.5)
                    network_trace = drain_network_observer(store_id, uninstall=True)
                    row["approve_network_trace"] = network_trace
                    logger.info(f"    被动网络摘要: {len(network_trace)} 条")
                except Exception as trace_error:
                    row["approve_network_trace_error"] = str(trace_error)
                    logger.info(f"    被动网络摘要读取失败: {trace_error}")

        if approve_exception is not None and not approval_attempt_started:
            continue

        pending_recheck: dict[str, Any] | None = None
        pending_recheck_error = ""
        write_accepted = bool(approve_result.get("ok")) and approve_exception is None
        if write_source == "api":
            if not write_accepted:
                try:
                    pending_recheck = check_pending_application_api(
                        store_id,
                        apply_id,
                        expected_creator_id=str(row.get("creator_id") or ""),
                        expected_product_id=str(row.get("product_id") or ""),
                    )
                except Exception as error:
                    pending_recheck_error = str(error)
            outcome = decide_api_approval_outcome(
                approve_result=approve_result,
                approve_exception=approve_exception,
                pending_recheck=pending_recheck,
            )
        else:
            postcheck_status: dict[str, Any] | None = None
            for postcheck_attempt in range(2):
                try:
                    postcheck_status = check_pending_application_api(
                        store_id,
                        apply_id,
                        expected_creator_id=str(row.get("creator_id") or ""),
                        expected_product_id=str(row.get("product_id") or ""),
                    )
                    pending_recheck_error = ""
                except Exception as error:
                    postcheck_status = None
                    pending_recheck_error = str(error)

                if (
                    postcheck_status
                    and postcheck_status.get("ok")
                    and postcheck_status.get("state") == "not-found-in-pending"
                ):
                    break
                if postcheck_attempt == 0:
                    time.sleep(1.0)
            pending_recheck = postcheck_status
            if write_accepted or (
                postcheck_status
                and postcheck_status.get("ok")
                and postcheck_status.get("state") == "not-found-in-pending"
            ):
                outcome = {
                    "approve_status": "approved",
                    "action": "approved",
                    "approve_confirmation": "deferred",
                    "stop_round": False,
                }
            elif (
                postcheck_status
                and postcheck_status.get("ok")
                and postcheck_status.get("state") == "pending-approvable"
            ):
                outcome = {
                    "approve_status": "failed",
                    "action": "approve-failed",
                    "approve_confirmation": "still-pending",
                    "stop_round": True,
                }
            else:
                outcome = {
                    "approve_status": "unknown",
                    "action": "approve-unknown",
                    "approve_confirmation": "write-uncertain",
                    "stop_round": True,
                }

        row["approve_pending_recheck"] = pending_recheck or {
            "ok": False,
            "state": "skipped" if write_accepted else "verification-error",
            "error": pending_recheck_error,
        }
        row["approve_confirmation"] = outcome["approve_confirmation"]
        if write_source == "api":
            row["approve_detail"] = {
                "write_source": "api",
                "request": approve_result.get("request"),
                "response": approve_result.get("response"),
            }
        else:
            row["approve_detail"] = {
                "write_source": "dom",
                "note": approve_result.get("note"),
                "steps_summary": [
                    list(step.keys())[0]
                    for step in (approve_result.get("steps") or [])
                ],
            }

        if outcome["approve_status"] == "failed":
            row["approve_status"] = "failed"
            row["approve_error"] = (
                str(approve_exception)
                if approve_exception is not None
                else approve_result.get("error") or str(approve_result)
            )
            row["action"] = "approve-failed"
            logger.info(f"    批准失败，且只读 API 确认仍待审核: {row['approve_error']}")
            logger.info("    已停止本轮，禁止自动改试下一条申请。")
            break
        if outcome["approve_status"] != "approved":
            row["approve_status"] = "unknown"
            row["approve_error"] = (
                f"{write_source} 批准写结果不确定；禁止重试及写飞书"
            )
            row["action"] = "approve-unknown"
            logger.info(
                "    批准状态未知：已停止本轮，禁止自动重试；"
                "请根据导出 approve_pending_recheck 人工核对。")
            break

        row["approve_status"] = "approved"
        row["action"] = "approved"
        row["approved_at"] = datetime.now().isoformat(timespec="seconds")
        approval_note = approve_result.get("note") or (
            "write-accepted"
            if write_source == "api"
            else "dom-accepted"
        )
        logger.info(
            f"    批准成功 note={approval_note}；"
            f"平台「待发货」约 {PLATFORM_STATUS_LAG_MINUTES} 分钟后刷新，"
            "列表确认已延后")
        processed += 1

        if not write_feishu or not bitable_token:
            row["feishu_status"] = "skipped-no-write"
            if execute_delay:
                time.sleep(execute_delay)
            continue

        try:
            created = create_creator_relation_record(
                bitable_token,
                creator_handle=creator_name,
                followers_raw=format_followers_raw(row),
                fulfillment_raw=format_fulfillment_raw(row),
                sample_product=str(product_resolve.get("option")),
                app_token=app_token,
                table_id=table_id,
            )
            record_id = created.get("record_id")
            row["feishu_status"] = "created"
            row["feishu_record_id"] = record_id
            row["action"] = "approved+feishu"
            if record_id:
                feishu_created_ids.append(str(record_id))
            logger.info(f"    飞书写入成功 record_id={record_id}")
        except FeishuBitableError as error:
            row["feishu_status"] = "error"
            row["feishu_error"] = str(error)
            row["action"] = "approved+feishu-failed"
            logger.info(
                f"    飞书写入失败（批准已成功，需人工补录）: {error}")

        if execute_delay:
            time.sleep(execute_delay)

    if feishu_created_ids:
        logger.info(
            f"[回退提示] 本轮新建飞书 record_id: {', '.join(feishu_created_ids)}；"
            f"如需撤销可调用 delete_record / 在表中删除")
    return candidates


def _run_confirm_pipeline(
    *,
    store_id: str,
    candidates: list[dict[str, Any]],
    hero_data: dict[str, Any] | None,
    write_feishu: bool,
    execute_limit: int,
    config_path: str | None,
) -> list[dict[str, Any]]:
    """延后确认平台列表是否已转入待发货；可选补写飞书。"""
    product_id_to_sku = build_product_id_to_sku_map(hero_data or {})
    sample_options: list[str] = []
    bitable_token: str | None = None
    app_token = DEFAULT_APP_TOKEN
    table_id = DEFAULT_TABLE_ID

    if write_feishu:
        try:
            from lib.app_config import load_bitable_settings

            bitable_settings = load_bitable_settings(
                config_path=config_path,
                default_app_token=DEFAULT_APP_TOKEN,
                default_table_id=DEFAULT_TABLE_ID,
                default_view_id=DEFAULT_VIEW_ID,
            )
            app_token = bitable_settings.get("app_token") or DEFAULT_APP_TOKEN
            table_id = bitable_settings.get("table_id") or DEFAULT_TABLE_ID
            bitable_token = get_bitable_access_token(config_path=config_path)
            sample_options = list_sample_product_options(
                bitable_token,
                app_token=app_token,
                table_id=table_id,
            )
            logger.info(
                f"[飞书] 延后确认可补写 table={table_id} "
                f"寄样产品选项={len(sample_options)} 个")
        except (FeishuBitableError, Exception) as error:
            logger.error(f"[飞书] 初始化失败，将只确认不写表: {error}")
            write_feishu = False

    limit = execute_limit if execute_limit > 0 else DEFAULT_EXECUTE_LIMIT
    processed = 0
    for row in candidates:
        if processed >= limit:
            row["approve_confirmation"] = "skipped-limit"
            continue

        apply_id = str(row.get("apply_id") or "").strip()
        creator_name = str(row.get("creator_name") or "").strip()
        logger.info(f"  [延后确认] {creator_name} apply={apply_id}")
        try:
            confirmation = confirm_application_approved_api(
                store_id,
                apply_id,
                expected_creator_id=str(row.get("creator_id") or ""),
                expected_product_id=str(row.get("product_id") or ""),
                max_attempts=1,
            )
        except Exception as error:
            row["approve_confirmation"] = "error"
            row["approve_postcheck"] = {"ok": False, "error": str(error)}
            logger.info(f"    列表确认失败: {error}")
            continue

        row["approve_postcheck"] = confirmation
        if confirmation.get("state") == "approved":
            row["approve_status"] = "approved"
            row["approve_confirmation"] = "confirmed"
            row["approve_error"] = ""
            logger.info("    平台列表已转入待发货")
            processed += 1
        elif confirmation.get("state") == "still-pending":
            row["approve_confirmation"] = "still-pending"
            logger.info("    仍在待审核；请人工核对，不自动重批")
            processed += 1
            continue
        else:
            row["approve_confirmation"] = str(confirmation.get("state") or "unknown")
            logger.info(
                f"    列表仍未确认 state={row['approve_confirmation']}；"
                "不改写批准结论，也不自动重批")
            processed += 1
            continue

        already_written = row.get("feishu_status") in {"created", "approved+feishu"}
        if not write_feishu or not bitable_token or already_written:
            continue

        not_hero = _reject_if_not_exact_hero(row, hero_data)
        if not_hero:
            row["feishu_status"] = "skipped"
            row["feishu_error"] = not_hero
            logger.info(f"    飞书补写跳过: {not_hero}")
            continue

        product_resolve = resolve_sample_product_for_row(
            row,
            product_id_to_sku=product_id_to_sku,
            sample_product_options=sample_options
            or list(product_id_to_sku.values()),
        )
        if sample_options:
            product_resolve = resolve_sample_product_for_row(
                row,
                product_id_to_sku=product_id_to_sku,
                sample_product_options=sample_options,
            )
        if not product_resolve.get("ok"):
            row["feishu_status"] = "skipped"
            row["feishu_error"] = product_resolve.get("reason")
            logger.info(f"    飞书补写跳过: {product_resolve.get('reason')}")
            continue
        try:
            dup = find_duplicate_record(
                bitable_token,
                creator_handle=creator_name,
                sample_product=str(product_resolve.get("option")),
                app_token=app_token,
                table_id=table_id,
            )
        except FeishuBitableError as error:
            row["feishu_status"] = "error"
            row["feishu_error"] = f"查重失败: {error}"
            logger.info(f"    飞书补写查重失败: {error}")
            continue
        if dup:
            row["feishu_status"] = "duplicate"
            row["feishu_record_id"] = dup.get("record_id")
            logger.info(f"    飞书已存在 record={dup.get('record_id')}")
            continue
        try:
            created = create_creator_relation_record(
                bitable_token,
                creator_handle=creator_name,
                followers_raw=format_followers_raw(row),
                fulfillment_raw=format_fulfillment_raw(row),
                sample_product=str(product_resolve.get("option")),
                app_token=app_token,
                table_id=table_id,
            )
            row["feishu_status"] = "created"
            row["feishu_record_id"] = created.get("record_id")
            row["action"] = "approved+feishu"
            logger.info(f"    飞书补写成功 record_id={created.get('record_id')}")
        except FeishuBitableError as error:
            row["feishu_status"] = "error"
            row["feishu_error"] = str(error)
            logger.error(f"    飞书补写失败: {error}")
    return candidates


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "样品申请筛查（默认只读导出；"
            "--execute --yes 可批准；批准默认调用 API；--write-feishu 才写达人关系管理(新)）"
        ),
    )
    ap.add_argument("--store-id", default=None, help=f"默认测试 1 号店 {DEFAULT_TEST_STORE_ID}")
    ap.add_argument("--store-name", default=None)
    ap.add_argument("--no-default-store", action="store_true")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--max-rows", type=int, default=0, help="最多处理 N 条（0=不限；测试建议小）")
    ap.add_argument("--page-wait", type=float, default=2.0)
    ap.add_argument(
        "--from-seller-home",
        action="store_true",
        help=(
            "从已登录的 TikTok Shop 商家中心首页自动导航到样品申请-待审核；"
            "须显式传 --store-id"
        ),
    )
    ap.add_argument(
        "--data-source",
        choices=DATA_SOURCE_CHOICES,
        default="dom",
        help=(
            "列表和筛查详情数据源：dom=现有路径；api=页面同源接口；"
            "auto=API失败回退DOM；shadow=双读对比且DOM为权威（默认 dom）"
        ),
    )
    ap.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认仓库根 config.toml；也可用 ZN_SAMPLE_CONFIG）",
    )
    ap.add_argument(
        "--hero-feishu-url",
        default=None,
        help=f"飞书主推表 wiki/sheets 链接（默认 {DEFAULT_HERO_URL}；也可用 FEISHU_HERO_URL / config.toml）",
    )
    ap.add_argument(
        "--hero-sheet",
        default=None,
        help=f"飞书电子表格子表名（默认 {DEFAULT_SHEET_TITLE}；也可用 FEISHU_HERO_SHEET / config.toml）",
    )
    ap.add_argument(
        "--feishu-app-id",
        default=None,
        help=f"飞书应用 App ID（默认 {DEFAULT_APP_ID}；也可用 FEISHU_APP_ID / config.toml）",
    )
    ap.add_argument(
        "--feishu-app-secret",
        default=None,
        help="飞书 App Secret（优先 CLI；否则环境变量 / config.toml；勿提交 git）",
    )
    ap.add_argument(
        "--skip-hero-check",
        action="store_true",
        help="跳过主推条件（放松 SOP；正式筛查禁止）",
    )
    ap.add_argument(
        "--with-detail",
        action="store_true",
        help="对筛查通过(列表层)的达人再进详情页拉视频/直播 GPM（只读）",
    )
    ap.add_argument(
        "--detail-all",
        action="store_true",
        help="对所有扫到的行拉详情（更慢；仍只读）",
    )
    ap.add_argument("--detail-delay", type=float, default=1.0, help="详情间间隔秒")
    ap.add_argument(
        "--require-detail",
        action="store_true",
        help="无详情指标则判定不通过",
    )
    ap.add_argument("--eligible-only", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--from-export",
        nargs="?",
        const="latest",
        default=None,
        help="用已有筛查导出 json 直接批准；不写路径则用 exports/ 最新一份",
    )
    ap.add_argument(
        "--confirm-export",
        action="store_true",
        help=(
            "只对 --from-export 里已批行做延后列表确认（默认等满约 "
            f"{PLATFORM_STATUS_LAG_MINUTES} 分钟）；不重新批准"
        ),
    )
    ap.add_argument(
        "--force-confirm",
        action="store_true",
        help="延后确认时忽略平台约 10 分钟刷新窗口",
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="危险：对筛查通过行批准（默认调用 API；须同时 --yes；默认仍只导出）",
    )
    ap.add_argument(
        "--yes",
        action="store_true",
        help="与 --execute 联用，确认真实批准（缺一不可）",
    )
    ap.add_argument(
        "--execute-limit",
        type=int,
        default=DEFAULT_EXECUTE_LIMIT,
        help=f"最多批准 N 条（默认 {DEFAULT_EXECUTE_LIMIT}；0 视为 {DEFAULT_EXECUTE_LIMIT}）",
    )
    ap.add_argument(
        "--execute-delay",
        type=float,
        default=1.5,
        help="每条批准间隔秒（默认 1.5）",
    )
    ap.add_argument(
        "--write-feishu",
        action="store_true",
        help="批准成功后写入飞书「达人关系管理(新)」（测试环境请勿默认开启）",
    )
    ap.add_argument(
        "--observe-approve-network",
        action="store_true",
        help="仅 execute 时：被动记录批准相关请求的脱敏结构；不主动重放请求",
    )
    ap.add_argument(
        "--write-source",
        choices=("dom", "api"),
        default="api",
        help=(
            "批准写入路径：api=已捕获验证的页面同源批准接口（默认）；"
            "dom=列表页面点击备用路径"
        ),
    )
    ap.add_argument(
        "--no-pre-backup",
        action="store_true",
        help="execute 时跳过批准前本地备份（不推荐）",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="终端打印页面 API / 扫表明细（业务员入口默认不打）",
    )
    args = ap.parse_args()
    configure_logging(verbose=bool(args.verbose))
    set_verbose(bool(args.verbose))
    try:
        args.from_export = resolve_from_export_arg(args.from_export)
    except FileNotFoundError as error:
        logger.error(str(error))
        return 2

    if args.confirm_export and not args.from_export:
        logger.error("--confirm-export 必须配合 --from-export")
        return 2
    if args.confirm_export and args.execute:
        logger.error("--confirm-export 只做延后确认，不能同时 --execute")
        return 2
    if args.from_export and not args.execute and not args.confirm_export:
        logger.error(
            "--from-export 仅用于批准或延后确认；"
            "请加 --execute --yes，或改用 --confirm-export"
        )
        return 2
    if args.from_export and args.observe_approve_network:
        logger.error("--from-export 不支持 --observe-approve-network")
        return 2
    if args.force_confirm and not args.confirm_export:
        logger.error("--force-confirm 仅适用于 --confirm-export")
        return 2
    if args.execute and not args.yes:
        logger.error(
            "将真实点击「同意」。确认请加 --yes，或去掉 --execute 做只读筛查。"
        )
        return 2
    if args.write_feishu and not args.execute and not args.confirm_export:
        logger.error("--write-feishu 仅在 --execute --yes 或 --confirm-export 时有效")
        return 2
    if args.observe_approve_network and not args.execute:
        logger.error("--observe-approve-network 仅在 --execute --yes 时有效")
        return 2
    if args.observe_approve_network and args.write_source != "dom":
        logger.error(
            "--observe-approve-network 仅适用于 --write-source dom；"
            "API 写入使用已登记的窄接口"
        )
        return 2
    if args.execute and args.data_source == "shadow":
        logger.error("真实批准不能使用 --data-source shadow；请改用 auto 或 api。")
        return 2

    if args.confirm_export:
        mode = "CONFIRM"
    elif args.execute:
        mode = "EXECUTE"
    else:
        mode = "DRY-RUN/只读导出"
    logger.info("=" * 60)
    logger.info(f"样品申请筛查 | 模式={mode}")
    if args.confirm_export:
        limit_show = args.execute_limit if args.execute_limit > 0 else DEFAULT_EXECUTE_LIMIT
        logger.info(
            f"  延后确认: 开 | limit={limit_show} | "
            f"写飞书={'开' if args.write_feishu else '关（测试默认）'} | "
            f"强制忽略等待={'开' if args.force_confirm else '关'}")
        logger.info(
            f"  说明: 不重新批准；平台「待发货」约 {PLATFORM_STATUS_LAG_MINUTES} 分钟后刷新")
        logger.info(f"  输入: 复用导出 {args.from_export}")
    elif args.execute:
        limit_show = args.execute_limit if args.execute_limit > 0 else DEFAULT_EXECUTE_LIMIT
        logger.info(
            f"  批准: 开 | limit={limit_show} | "
            f"写入路径={args.write_source} | "
            f"写飞书={'开' if args.write_feishu else '关（测试默认）'}")
        logger.info("  顺序: 写接口接受即视为同意成功 → 再写飞书；列表确认延后")
        if args.from_export:
            logger.info(f"  输入: 复用导出 {args.from_export}（跳过扫表/详情）")
    else:
        logger.info("  默认只读导出 | **未**启用同意")
    if not args.from_export:
        logger.info(f"  列表和筛查详情数据源: {args.data_source}")
    logger.info("主推表来源: 飞书云文档（不再使用本地 xlsx）")
    if args.from_seller_home:
        logger.info("前提：目标店已登录，并停在 TikTok Shop 商家中心首页")
    else:
        logger.info("前提：你已手动打开「样品申请 → 待审核」并停在该页")
    logger.info("=" * 60)

    explicit_store = bool(
        str(args.store_id or "").strip() or str(args.store_name or "").strip()
    )
    # 从首页导航时：只开着一家店就用那家，不要静默落到测试 1 号店。
    default_sid = None if args.no_default_store else DEFAULT_TEST_STORE_ID
    if args.from_seller_home and not explicit_store:
        default_sid = None
    try:
        store_id = resolve_store_id(
            store_id=args.store_id,
            store_name=args.store_name,
            default_store_id=default_sid,
        )
    except Exception as e:
        logger.error(f"解析店铺失败: {e}")
        return 2

    if store_id == DEFAULT_TEST_STORE_ID:
        logger.info(f"[测试环境] 1 号店 {DEFAULT_TEST_STORE_NAME} ({store_id})")

    if args.from_seller_home:
        try:
            navigation_result = navigate_from_seller_home_to_pending(
                store_id,
                navigation_timeout=45.0,
                poll_interval=max(0.5, min(2.0, args.page_wait)),
            )
        except Exception as error:
            logger.error(f"自动导航失败: {error}")
            return 2
        destination = navigation_result.get("destination") or {}
        logger.info(
            f"[自动导航] 已进入样品申请-待审核 "
            f"shop_id={destination.get('shop_id')} "
            f"region={destination.get('shop_region')}")

    # 主推表：仅飞书
    hero_keys: set[str] = set()
    hero_data: dict[str, Any] | None = None
    if args.skip_hero_check:
        logger.info("[主推] --skip-hero-check：跳过飞书主推条件（非正式）")
    else:
        try:
            hero_data = load_hero_from_feishu(
                url=args.hero_feishu_url,
                sheet_title=args.hero_sheet,
                app_id=args.feishu_app_id,
                app_secret=args.feishu_app_secret,
                config_path=args.config,
            )
        except FeishuHeroError as error:
            logger.error(f"飞书主推表读取失败: {error}")
            logger.info(
                "请确认：1) config.toml [feishu].app_secret 或 FEISHU_APP_SECRET  "
                "2) 应用仍有表格可阅读权限  "
                "3) 链接/子表名正确")
            return 2
        except Exception as error:
            logger.error(f"飞书主推表读取异常: {error}")
            return 2

        hero_keys = set(hero_data.get("hero_keys") or set())
        hero_skus = hero_data.get("hero_skus") or []
        hero_product_ids = hero_data.get("hero_product_ids") or []
        preview = ", ".join(str(sku) for sku in hero_skus[:20])
        if len(hero_skus) > 20:
            preview = f"{preview}, …"
        cfg_note = hero_data.get("config_path") or "（无 config.toml，仅用环境变量/默认）"
        logger.info(f"[配置] {cfg_note}")
        logger.info(
            f"[主推飞书] title={hero_data.get('doc_title') or hero_data.get('sheet_title')} "
            f"sheet={hero_data.get('sheet_title')} "
            f"token={hero_data.get('spreadsheet_token')} "
            f"→ 货号 {len(hero_data.get('all_skus') or [])} 个，"
            f"主推货号 {len(hero_skus)} 个，主推商品ID {len(hero_product_ids)} 个，"
            f"匹配键 {len(hero_keys)} 个"
            + (f"; 主推货号: {preview}" if preview else ""))
        if not hero_keys:
            logger.info(
                "警告: 飞书表已读到，但「是否主推=是」为空；条件2 将全部判非主推")

    criteria = Criteria(require_hero_sku=not args.skip_hero_check)
    t0 = time.time()
    pending_result = None
    raw_rows: list[dict[str, Any]] = []
    pre: list[dict[str, Any]] = []
    detail_targets: list[dict] = []
    detail_shadow_reports: list[dict[str, Any]] = []
    strict_api_detail_failure_count = 0

    if args.from_export:
        try:
            pre = _load_export_rows(args.from_export)
        except Exception as error:
            logger.error(f"读取导出失败: {error}")
            return 2
        if not pre:
            logger.error("导出没有可用行")
            return 1
        raw_rows = pre
        logger.info(
            f"[输入] 导出 {args.from_export} → {len(pre)} 行；"
            + (
                "跳过扫表和详情，仅做延后列表确认"
                if args.confirm_export
                else "跳过扫表和详情，仅对通过行做批准前状态预检"
            ))
        if args.with_detail or args.detail_all or args.require_detail:
            logger.info("[输入] --from-export 已忽略 --with-detail / --detail-all / --require-detail")
    else:
        try:
            pending_result = load_pending_rows(
                store_id,
                data_source=args.data_source,
                max_pages=args.max_pages,
                max_rows=args.max_rows,
                page_wait=args.page_wait,
            )
            raw_rows = pending_result.rows
        except Exception as e:
            logger.error(f"扫表失败: {e}")
            return 1

        logger.info(f"[数据源] 实际使用={pending_result.source_used}")
        if pending_result.fallback_reason:
            logger.info(f"[数据源] fallback={pending_result.fallback_reason}")

        if not raw_rows:
            logger.error("未读到待审核行")
            return 1

    if not args.from_export:
        list_href = (
            (raw_rows[0].get("_list_href") or raw_rows[0].get("href") or "")
            if raw_rows
            else ""
        )

        # 先做列表层初筛，并保留阶段结果供后续导出。初筛淘汰者不会因为未拉详情，
        # 被追加“缺少达人详情”等与其实际淘汰原因无关的终筛失败。
        pre = [
            evaluate_row(
                r,
                criteria=criteria,
                hero_keys=hero_keys,
                skip_hero_check=args.skip_hero_check,
                require_detail=False,
            )
            for r in raw_rows
        ]
        for row in pre:
            initial_failure_reason = "; ".join(row.get("fail_reasons") or [])
            row["initial_screen_eligible"] = bool(row.get("eligible"))
            row["initial_screen_failure_reason"] = initial_failure_reason
            if row["initial_screen_eligible"]:
                row["screening_stage"] = "初筛通过，待详情复筛"
            else:
                row["screening_stage"] = "初筛淘汰"
                row["reason"] = f"初筛淘汰：{initial_failure_reason}"

        # 详情：只读
        need_detail = args.with_detail or args.detail_all or args.require_detail
        if need_detail:
            if args.detail_all:
                detail_targets = list(pre)
            else:
                # 列表层已过的再拉详情（禁止 --detail-limit 截断；要限量用 --max-rows）
                detail_targets = [x for x in pre if x.get("eligible")]
            logger.info(f"详情复筛：共 {len(detail_targets)} 人")

            by_key = {
                (x.get("apply_id") or x.get("creator_name")): x for x in pre
            }
            for i, target in enumerate(detail_targets, 1):
                key = target.get("apply_id") or target.get("creator_name")
                logger.info(
                    f"  [{i}/{len(detail_targets)}] {target.get('creator_name')}")
                # 合并 raw 字段给 fetch
                src = next(
                    (
                        r
                        for r in raw_rows
                        if str(r.get("apply_id")) == str(target.get("apply_id"))
                        or r.get("creator_name") == target.get("creator_name")
                    ),
                    target,
                )
                try:
                    detail_result = load_creator_detail(
                        store_id,
                        src,
                        data_source=args.data_source,
                        list_href=list_href,
                        wait=max(3.5, args.page_wait + 1.5),
                    )
                    res = detail_result.result
                except Exception as e:
                    logger.info(f"    失败: {e}")
                    target["detail_error"] = str(e)
                    if args.data_source == "api":
                        strict_api_detail_failure_count += 1
                    continue
                if is_verbose():
                    logger.info(f"    详情数据源={detail_result.source_used}")
                if detail_result.shadow_report:
                    detail_shadow_reports.append(detail_result.shadow_report)
                if not res.get("ok"):
                    logger.info(f"    失败: {res.get('error')}")
                    target["detail_error"] = res.get("error")
                    if args.data_source == "api":
                        strict_api_detail_failure_count += 1
                else:
                    d = res["detail"]
                    if is_verbose():
                        logger.info(
                            f"    VideoGPM={d.get('video_gpm')} LiveGPM={d.get('live_gpm')} "
                            f"avgViews={d.get('avg_video_views')} eng={d.get('video_engagement')} "
                            f"type={d.get('creator_type')}")
                    # 写回 pre 行
                    row = by_key.get(key) or target
                    for k in (
                        "video_gpm",
                        "live_gpm",
                        "avg_video_views",
                        "video_engagement",
                        "avg_live_views",
                        "live_engagement",
                        "est_post_rate",
                        "overall_gpm",
                        "revenue_per_buyer",
                        "creator_type",
                        "video_gpm_n",
                        "live_gpm_n",
                        "avg_video_views_n",
                        "avg_live_views_n",
                        "video_engagement_n",
                        "live_engagement_n",
                        "overall_gpm_n",
                        "aov_detail_n",
                        "est_post_rate_n",
                        "has_cn_video_card",
                        "has_en_video_card",
                        "extract_via",
                        "text_head",
                        "profile_type_field_counts",
                        "_detail_data_source",
                        "_detail_fallback_reason",
                    ):
                        if k in d:
                            row[k] = d[k]
                    row["detail_checked"] = True
                    if not d.get("video_gpm") and not d.get("live_gpm") and is_verbose():
                        logger.info(
                            f"    警告: 视频/直播GPM仍为空 "
                            f"cn_card={d.get('has_cn_video_card')} "
                            f"en_card={d.get('has_en_video_card')} "
                            f"overall={d.get('overall_gpm')} "
                            f"via={d.get('extract_via')}")
                if args.detail_delay:
                    time.sleep(args.detail_delay)

            # 带详情重判。只有初筛通过者才要求详情指标；初筛淘汰者保留原始原因，
            # 避免导出出现并未获取详情所产生的额外 failure。
            reevaluated_rows: list[dict[str, Any]] = []
            for row in pre:
                if not row.get("initial_screen_eligible"):
                    reevaluated_rows.append(row)
                    continue

                reevaluated_row = evaluate_row(
                    row,
                    criteria=criteria,
                    hero_keys=hero_keys,
                    skip_hero_check=args.skip_hero_check,
                    require_detail=args.require_detail,
                )
                reevaluated_row["initial_screen_eligible"] = True
                reevaluated_row["initial_screen_failure_reason"] = ""
                if reevaluated_row.get("eligible"):
                    reevaluated_row["screening_stage"] = "终筛通过"
                else:
                    reevaluated_row["screening_stage"] = "详情复筛淘汰"
                    final_failure_reason = "; ".join(
                        reevaluated_row.get("fail_reasons") or []
                    )
                    reevaluated_row["reason"] = f"详情复筛淘汰：{final_failure_reason}"
                reevaluated_rows.append(reevaluated_row)
            pre = reevaluated_rows

    passed = [x for x in pre if x.get("eligible")]
    logger.info(
        f"--- 判定: 合计={len(pre)} 通过={len(passed)} "
        f"用时 {time.time() - t0:.1f}s ---")
    for x in passed[:30]:
        logger.info(
            f"  [通过] {x.get('creator_name')}\t"
            f"粉={x.get('followers_n')}\tGMV={x.get('gmv_n')}\t"
            f"件={x.get('units_n')}\t履约={x.get('fulfillment_n')}\t"
            f"VGPM={x.get('video_gpm_n')}\tLGPM={x.get('live_gpm_n')}\t"
            f"type={x.get('creator_type')}\thero={x.get('is_hero')}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_prefix = args.out or (ROOT / "exports" / f"sample_screen_{ts}")

    if pending_result and pending_result.shadow_report:
        pending_result.shadow_report["detail_reports"] = detail_shadow_reports
        pending_result.shadow_report["detail_summary"] = {
            "count": len(detail_shadow_reports),
            "ok_count": sum(
                1 for report in detail_shadow_reports if report.get("ok")
            ),
            "mismatch_count": sum(
                len(report.get("mismatches") or [])
                for report in detail_shadow_reports
            ),
            "dom_only_count": sum(
                len(report.get("dom_only_fields") or [])
                for report in detail_shadow_reports
            ),
            "api_more_complete_count": sum(
                1
                for report in detail_shadow_reports
                if report.get("comparison_status") == "api-more-complete"
            ),
            "no_comparable_metrics_count": sum(
                1
                for report in detail_shadow_reports
                if report.get("comparison_status") == "no-comparable-metrics"
            ),
        }
        shadow_path = out_prefix.with_name(out_prefix.name + "_shadow.json")
        shadow_path.parent.mkdir(parents=True, exist_ok=True)
        shadow_path.write_text(
            json.dumps(
                pending_result.shadow_report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(f"[shadow] 差异报告: {shadow_path}")

    if args.confirm_export:
        candidates = _select_confirm_candidates_from_export(
            pre,
            force=bool(args.force_confirm),
        )
        logger.info(
            f"--- CONFIRM 目标 {len(candidates)}"
            f"（limit={args.execute_limit or DEFAULT_EXECUTE_LIMIT}）---")
        _run_confirm_pipeline(
            store_id=store_id,
            candidates=candidates,
            hero_data=hero_data,
            write_feishu=bool(args.write_feishu),
            execute_limit=args.execute_limit,
            config_path=args.config,
        )
    elif args.execute:
        # 批准前备份（可回看筛查结果；平台侧「同意」不可自动撤销）
        if not args.no_pre_backup:
            backup_paths = _write_backup(pre, out_prefix)
            logger.info("--- 批准前备份 ---")
            for key, path in backup_paths.items():
                logger.info(f"  {key}: {path}")

        # 合并 can_be_approved 等列表字段（evaluate 可能未保留）
        raw_by_apply = {
            str(item.get("apply_id") or ""): item for item in raw_rows if item.get("apply_id")
        }
        for row in pre:
            apply_key = str(row.get("apply_id") or "")
            source = raw_by_apply.get(apply_key) or {}
            if "can_be_approved" not in row and "can_be_approved" in source:
                row["can_be_approved"] = source.get("can_be_approved")
            if not row.get("follower_num") and source.get("follower_num") is not None:
                row["follower_num"] = source.get("follower_num")
            if not row.get("fulfillment_rate") and source.get("fulfillment_rate") is not None:
                row["fulfillment_rate"] = source.get("fulfillment_rate")

        if args.from_export:
            candidates = _select_execute_candidates_from_export(pre)
        else:
            candidates = [row for row in pre if row.get("eligible")]
        logger.info(
            f"--- EXECUTE 目标通过行 {len(candidates)}"
            f"（limit={args.execute_limit or DEFAULT_EXECUTE_LIMIT}）---")
        _run_execute_pipeline(
            store_id=store_id,
            candidates=candidates,
            hero_data=hero_data,
            write_feishu=bool(args.write_feishu),
            execute_limit=args.execute_limit,
            execute_delay=args.execute_delay,
            config_path=args.config,
            page_wait=args.page_wait,
            observe_approve_network=bool(args.observe_approve_network),
            write_source=args.write_source,
        )
        # 非通过行保持 export-only
        for row in pre:
            if not row.get("eligible") and not row.get("action"):
                row["action"] = "export-only"
                row["approve_forbidden"] = True
            elif row.get("eligible") and not row.get("action"):
                row["action"] = "export-only"
                row["approve_forbidden"] = True
    else:
        for row in pre:
            row["action"] = "export-only"
            row["approve_forbidden"] = True

    export_rows = (
        [row for row in pre if row.get("eligible")] if args.eligible_only else pre
    )
    paths = write_reports(export_rows, out_prefix)
    logger.info("--- 导出 ---")
    for key, path in paths.items():
        logger.info(f"  {key}: {path}")
    passed_count = sum(1 for row in pre if row.get("eligible"))
    if args.confirm_export:
        confirmed_count = sum(
            1 for row in pre if row.get("approve_confirmation") == "confirmed"
        )
        logger.info(
            f"完成。模式=CONFIRM 列表已确认={confirmed_count} "
            f"写飞书={'开' if args.write_feishu else '关'}。")
        summary_title = "「核对补写」完成"
        summary_stats = [
            f"列表已确认 : {confirmed_count}",
            f"已查看     : {len(pre)}",
            f"写飞书     : {'开' if args.write_feishu else '关'}",
        ]
        summary_hint = "日常请双击「2-筛查批准写飞书发私信」，不要拆开补写。"
    elif args.execute:
        approved_count = sum(1 for row in pre if row.get("approve_status") == "approved")
        logger.info(
            f"完成。模式=EXECUTE 批准成功={approved_count} "
            f"写飞书={'开' if args.write_feishu else '关'}。")
        logger.info(
            "回退：平台「同意」无法脚本撤销；飞书新建行见导出列 feishu_record_id / 控制台提示。"
            f"「待发货」约 {PLATFORM_STATUS_LAG_MINUTES} 分钟后刷新，"
            "可用 --confirm-export 延后核对。")
        summary_title = "「批准写飞书」完成"
        summary_stats = [
            f"批准成功 : {approved_count}",
            f"已查看   : {len(pre)}",
            f"写飞书   : {'开' if args.write_feishu else '关'}",
        ]
        summary_hint = (
            f"平台「待发货」约 {PLATFORM_STATUS_LAG_MINUTES} 分钟后才刷新；"
            "日常请双击「2-筛查批准写飞书发私信」，它会自动等。"
        )
    else:
        logger.info("完成。未执行任何同意/批准/拒绝操作。")
        summary_title = "「筛查名单」完成"
        summary_stats = [
            f"通过   : {passed_count}",
            f"已查看 : {len(pre)}",
            "模式   : 筛查（不会批准）",
        ]
        summary_hint = "若要批准并发介绍，双击「2-筛查批准写飞书发私信」。"
    logger.info(
        "\n"
        + format_job_summary(
            title=summary_title,
            stats=summary_stats,
            csv_path=paths.get("csv"),
            json_path=paths.get("json"),
            xlsx_path=paths.get("xlsx"),
            root=ROOT,
            hint=summary_hint,
        ))
    if strict_api_detail_failure_count:
        logger.info(
            "纯 API 模式存在详情读取失败："
            f"{strict_api_detail_failure_count}/{len(detail_targets)}。"
            "已保留导出用于排查，本次返回非零状态。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
