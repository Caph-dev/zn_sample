#!/usr/bin/env python3
"""自动审批：自定义审核方案的只读筛查、限量批准与核对补写。

危险路径（须显式）：
  --mode execute --yes --limit N [--write-feishu 1]
  --mode reconcile --yes [--write-feishu 1]

硬性纪律：
  - 默认只读（preview）；execute/reconcile 缺 --yes 直接退出
  - 只接受服务器生成的自定义规则快照与预览信封；不接收 shell 字符串
  - 批准走既有窄 API（--write-source api 固定）；批准成功再写飞书
  - 内容审查关闭时证据标记为 not_checked；执行边界要求规则快照与信封一致
  - 不发送介绍/物流私信，不自动切店、不重开店
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.app_log import configure_logging  # noqa: E402
from lib.auto_approval_rules import (  # noqa: E402
    CONTENT_DISABLED_REASON,
    SCHEMA_VERSION,
    AutoApprovalRuleError,
    rule_hash,
    rule_summary_lines,
    validate_custom_rule,
    verify_preview_envelope,
    verify_row_evidence,
)
from lib.creator_video_review import review_creator_rows  # noqa: E402
from lib.auto_approval_sku import evaluate_product_sku  # noqa: E402
from lib.filters import enrich_row  # noqa: E402
from lib.sample_navigation import navigate_from_seller_home_to_pending  # noqa: E402

# 复用正式筛查脚本的已验证链路（AGENTS.md：引用、不复制大段）
from screen_sample_requests import (  # noqa: E402
    DEFAULT_EXECUTE_LIMIT,
    FEISHU_RELATION_FIELD,
    PLATFORM_CONFIRMATION_FIELD,
    _reject_if_not_exact_hero,
    _run_confirm_pipeline,
    _select_confirm_candidates_from_export,
    _select_feishu_reconcile_rows_from_export,
    _select_order_backfill_rows_from_export,
    _set_feishu_relation_status,
    _set_platform_confirmation_status,
    _write_backup,
    decide_api_approval_outcome,
)
from lib.auto_approval_rules import evaluate_custom_row  # noqa: E402
from lib.feishu_bitable import (  # noqa: E402
    DEFAULT_APP_TOKEN,
    DEFAULT_TABLE_ID,
    DEFAULT_VIEW_ID,
    FeishuBitableError,
    build_product_id_to_sku_map,
    create_creator_relation_record,
    format_followers_raw,
    format_fulfillment_raw,
    get_bitable_access_token,
    list_sample_product_options,
    resolve_duplicate_record,
    resolve_sample_product_for_row,
)
from lib.feishu_hero import FeishuHeroError, load_hero_from_feishu  # noqa: E402
from lib.sample_api import check_pending_application_api  # noqa: E402
from lib.sample_data_source import (  # noqa: E402
    SYSTEMIC_DETAIL_FAILURE_LIMIT,
    is_systemic_detail_error,
    load_creator_detail,
    load_pending_rows,
)
from lib.sample_write_api import approve_application_api  # noqa: E402

logger = logging.getLogger(__name__)

PREVIEW_DATA_SOURCE = "auto"
EXECUTE_DELAY_SECONDS = 1.5


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON 不是对象: {path}")
    return data


def _load_hero(store_id: str, config_path: str | None) -> dict[str, Any]:
    """读取飞书主推表（与正式筛查同源）。"""
    try:
        return load_hero_from_feishu(
            url=None,
            sheet_title=None,
            app_id=None,
            app_secret=None,
            config_path=config_path,
        )
    except FeishuHeroError as error:
        raise RuntimeError(f"飞书主推表读取失败: {error}") from error
    except Exception as error:
        raise RuntimeError(f"飞书主推表读取异常: {error}") from error


def _hero_allowed_product_ids(hero_data: dict[str, Any]) -> set[str]:
    product_id_to_sku = build_product_id_to_sku_map(hero_data or {})
    allowed = {
        pid
        for pid in (hero_data or {}).get("hero_product_ids") or []
        if str(pid).strip()
    }
    allowed.update(product_id_to_sku.keys())
    return allowed


def _overlay_detail_fields(row: dict[str, Any], detail: dict[str, Any]) -> None:
    for key in (
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
    ):
        if key in detail:
            row[key] = detail[key]
    row["detail_checked"] = True


def _final_content_verdict(
    row: dict[str, Any],
    *,
    content_enabled: bool,
    require_display: bool,
    min_related: int,
) -> str:
    """内容审核在自定义规则下的三态结论。"""
    if not content_enabled:
        row["content_review_status"] = "not_run"
        row["content_review_reason"] = CONTENT_DISABLED_REASON
        return "not_checked"
    status = str(row.get("content_review_status") or "")
    if status == "passed":
        return "passed"
    complete = bool(row.get("content_review_complete"))
    related_count = int(row.get("content_review_related_count") or 0)
    if (
        not require_display
        and complete
        and related_count >= min_related
        and status == "needs_review"
    ):
        row["content_review_reason"] = (
            f"{row.get('content_review_reason')}|display-evidence-not-required"
        )
        return "passed"
    if status == "failed":
        return "failed"
    return "needs_review"


def _run_preview(args: argparse.Namespace) -> int:
    store_id = args.store_id
    rules_payload = _load_json(args.rules)
    if args.from_seller_home:
        try:
            navigate_from_seller_home_to_pending(
                store_id,
                navigation_timeout=90.0,
                poll_interval=max(0.5, min(2.0, args.page_wait)),
            )
        except Exception as error:
            logger.error(f"自动导航失败: {error}")
            return 2

    hero_data = _load_hero(store_id, args.config)
    hero_keys = set(hero_data.get("hero_keys") or set())
    allowed_product_ids = _hero_allowed_product_ids(hero_data)
    try:
        rule = validate_custom_rule(
            rules_payload,
            allowed_product_ids=allowed_product_ids,
        )
    except AutoApprovalRuleError as error:
        logger.error(f"规则校验失败: {error.code} {error}")
        return 2

    rule_summary = rule_summary_lines(
        rule,
        product_display=build_product_id_to_sku_map(hero_data or {}),
    )
    for line in rule_summary:
        logger.info(f"[规则] {line}")

    started_at = datetime.now().isoformat(timespec="seconds")
    integrity_notes: list[str] = []
    integrity_failures: list[str] = []
    try:
        pending_result = load_pending_rows(
            store_id,
            data_source=PREVIEW_DATA_SOURCE,
            max_pages=args.max_pages,
            max_rows=args.max_rows,
            page_wait=args.page_wait,
        )
        raw_rows = pending_result.rows
    except Exception as error:
        logger.error(f"扫表失败: {error}")
        return 1
    logger.info(
        f"[数据源] 实际使用={pending_result.source_used}"
        + (f" fallback={pending_result.fallback_reason}" if pending_result.fallback_reason else "")
    )
    if not raw_rows:
        logger.error("未读到待审核行")
        return 1

    rows = [enrich_row(raw) for raw in raw_rows]

    # 视频/直播组启用才需要拉详情；其余条件不强拉（履约按列表口径可判）。
    need_detail = rule.video_live.enabled
    if need_detail:
        list_href = (raw_rows[0].get("_list_href") or raw_rows[0].get("href") or "")
        detail_targets = [
            row for row in rows if row.get("product_id") in rule.product_ids
        ]
        logger.info(f"视频/直播组启用：拉详情 {len(detail_targets)} 行")
        systemic_failure_reason = ""
        consecutive_systemic_failures = 0
        skipped_detail_rows = 0
        for index, row in enumerate(detail_targets, 1):
            if systemic_failure_reason:
                # 已确认系统级故障：不再逐行请求，也不回退 DOM（会白等）。
                row["detail_error"] = "detail-api-systemic-failure"
                skipped_detail_rows += 1
                continue
            logger.info(
                f"  [{index}/{len(detail_targets)}] {row.get('creator_name')}"
            )
            try:
                detail_result = load_creator_detail(
                    store_id,
                    row,
                    data_source=PREVIEW_DATA_SOURCE,
                    list_href=list_href,
                    wait=max(3.5, args.page_wait + 1.5),
                )
                result = detail_result.result
            except Exception as error:
                row["detail_error"] = str(error)
                integrity_failures.append(
                    f"详情失败 {row.get('creator_name')}: {error}"
                )
                continue
            if not result.get("ok"):
                row["detail_error"] = result.get("error")
                integrity_failures.append(
                    f"详情失败 {row.get('creator_name')}: {result.get('error')}"
                )
                if is_systemic_detail_error(
                    detail_result.fallback_reason or result.get("error")
                ):
                    consecutive_systemic_failures += 1
                    if consecutive_systemic_failures >= SYSTEMIC_DETAIL_FAILURE_LIMIT:
                        systemic_failure_reason = str(
                            detail_result.fallback_reason
                            or result.get("error")
                            or "detail-api-systemic-failure"
                        )
                else:
                    consecutive_systemic_failures = 0
                continue
            consecutive_systemic_failures = 0
            _overlay_detail_fields(row, result["detail"])
            if args.detail_delay:
                time.sleep(args.detail_delay)
        if systemic_failure_reason:
            logger.error(
                f"[详情数据源] 连续 {SYSTEMIC_DETAIL_FAILURE_LIMIT} 行命中同一系统级错误，"
                f"已停止逐行回退 DOM：{systemic_failure_reason[:160]}"
            )
            logger.error(
                f"[详情数据源] 剩余 {skipped_detail_rows} 行未取详情，按 needs_review 处理；"
                "请关闭浏览器插件或等平台恢复后重试"
            )
            integrity_failures.append(
                f"详情接口系统级故障（{systemic_failure_reason[:120]}）："
                f"剩余 {skipped_detail_rows} 行未取详情，按待复核处理"
            )
    else:
        integrity_notes.append(
            "视频/直播组未启用：未拉取达人详情（履约/GPM 按列表口径）"
        )

    selected_allowed = set(rule.product_ids)
    evaluated_rows: list[dict[str, Any]] = []
    for row in rows:
        row["product_id"] = str(row.get("product_id") or "")
        evaluation = evaluate_custom_row(
            row,
            rule,
            hero_keys=hero_keys,
            allowed_product_ids=selected_allowed,
        )
        row.update(
            {
                "custom_overall": evaluation["overall"],
                "custom_checks": evaluation["checks"],
                "safety_blocks": evaluation["safety_blocks"],
                "blocked": evaluation["blocked"],
                "custom_eligible": evaluation["custom_eligible"],
            }
        )
        evaluated_rows.append(row)

    # 内容审核：只对「自定义规则已通过且无拦截」的行运行（与正式链路一致）。
    review_targets = [
        row
        for row in evaluated_rows
        if row.get("custom_eligible") and row.get("custom_overall") == "passed"
    ]
    if rule.content.enabled:
        if review_targets:
            logger.info(
                f"[内容审核] 自定义通过 {len(review_targets)} 行，开始审核近期带货视频"
            )
            try:
                review_inputs = [dict(row) for row in review_targets]
                for review_input in review_inputs:
                    review_input["sales_eligible"] = True
                reviewed_rows = review_creator_rows(review_inputs)
                if len(reviewed_rows) != len(review_targets):
                    raise ValueError("内容审核返回行数不匹配")
                for original, reviewed in zip(review_targets, reviewed_rows):
                    for key in (
                        "content_review_status",
                        "content_review_reason",
                        "content_review_handle",
                        "content_review_window_start",
                        "content_review_window_end",
                        "content_review_related_count",
                        "content_review_complete",
                        "content_review_evidence_path",
                        "content_review_version",
                        "content_review_model",
                        "content_review_video_ids",
                    ):
                        original[key] = reviewed.get(key)
                    original["sales_eligible"] = True
            except Exception as error:
                logger.error("[内容审核] 审核未完成，相关行转待复核: %s", error)
                for row in review_targets:
                    row["content_review_status"] = "needs_review"
                    row["content_review_reason"] = f"内容审核异常: {error}"
        else:
            logger.info("[内容审核] 无自定义通过行，跳过内容审核")

    stats = {
        "rows": len(evaluated_rows),
        "passed": 0,
        "failed": 0,
        "needs_review": 0,
        "blocked": 0,
        "eligible": 0,
    }
    for row in evaluated_rows:
        content_verdict = "not_run"
        if row.get("custom_eligible") and row.get("custom_overall") == "passed":
            content_verdict = _final_content_verdict(
                row,
                content_enabled=rule.content.enabled,
                require_display=rule.content.require_display,
                min_related=rule.content.min_related,
            )
            if content_verdict == "failed":
                row["custom_overall"] = "failed"
                row["custom_eligible"] = False
            elif content_verdict == "needs_review":
                row["custom_overall"] = "needs_review"
                row["custom_eligible"] = False
        row["content_verdict"] = content_verdict
        row["observed_at"] = datetime.now().isoformat(timespec="seconds")
        if row.get("blocked"):
            stats["blocked"] += 1
        elif row.get("custom_eligible"):
            stats["eligible"] += 1
            stats["passed"] += 1
        elif row.get("custom_overall") == "failed":
            stats["failed"] += 1
        else:
            stats["needs_review"] += 1

    envelope = {
        "envelope_type": "auto_approval_preview",
        "schema_version": SCHEMA_VERSION,
        "mode": "custom",
        "preview_id": args.preview_id,
        "store_id": store_id,
        "rule": rule.to_dict(),
        "rule_hash": rule_hash(rule),
        "rule_summary": rule_summary,
        "allowed_product_ids": sorted(allowed_product_ids),
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "integrity_complete": bool(not integrity_failures),
        "integrity_notes": integrity_notes + integrity_failures,
        "stats": stats,
        "rows": evaluated_rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info(
        f"--- 自定义筛查: 合计={stats['rows']} 通过={stats['passed']} "
        f"不符合={stats['failed']} 待复核={stats['needs_review']} "
        f"拦截={stats['blocked']} ---"
    )
    logger.info(f"预览已写入: {args.out}")
    return 0


def _run_execute(args: argparse.Namespace) -> int:
    if not args.yes:
        logger.error("将真实点击「同意」。确认请加 --yes，或去掉 --mode execute。")
        return 2
    limit = args.limit if args.limit > 0 else DEFAULT_EXECUTE_LIMIT
    store_id = args.store_id
    rules_payload = _load_json(args.rules)
    preview = _load_json(args.preview)
    rule = validate_custom_rule(
        rules_payload,
        allowed_product_ids=set(preview.get("allowed_product_ids") or []),
    )
    try:
        verify_preview_envelope(
            preview,
            expected_store_id=store_id,
            expected_rule_hash=rule_hash(rule),
        )
    except AutoApprovalRuleError as error:
        logger.error(f"预览信封校验失败: {error.code} {error}")
        return 2

    if args.from_seller_home:
        try:
            navigate_from_seller_home_to_pending(
                store_id,
                navigation_timeout=90.0,
                poll_interval=max(0.5, min(2.0, args.page_wait)),
            )
        except Exception as error:
            logger.error(f"自动导航失败: {error}")
            return 2

    hero_data = _load_hero(store_id, args.config)
    hero_keys = set(hero_data.get("hero_keys") or set())
    selected_allowed = set(rule.product_ids)
    preview_rows = preview.get("rows") or []
    rows_by_apply = {
        str(row.get("apply_id") or ""): row for row in preview_rows
    }
    selected_apply_ids = _load_json(args.apply_ids)
    if not isinstance(selected_apply_ids, list):
        logger.error("apply-ids 文件必须是数组")
        return 2
    missing = [apply_id for apply_id in selected_apply_ids if apply_id not in rows_by_apply]
    if missing:
        logger.error(f"预览缺少所选申请: {missing}")
        return 2
    if len(selected_apply_ids) > limit:
        logger.error(
            f"所选 {len(selected_apply_ids)} 行超过 limit {limit}，请重新选择"
        )
        return 2
    if not selected_apply_ids:
        logger.error("未选择任何申请")
        return 2

    write_feishu = bool(args.write_feishu)
    product_id_to_sku = build_product_id_to_sku_map(hero_data or {})
    bitable_token: str | None = None
    app_token = DEFAULT_APP_TOKEN
    table_id = DEFAULT_TABLE_ID
    sample_options: list[str] = []
    if write_feishu:
        try:
            from lib.app_config import load_bitable_settings

            bitable_settings = load_bitable_settings(
                config_path=args.config,
                default_app_token=DEFAULT_APP_TOKEN,
                default_table_id=DEFAULT_TABLE_ID,
                default_view_id=DEFAULT_VIEW_ID,
            )
            app_token = bitable_settings.get("app_token") or DEFAULT_APP_TOKEN
            table_id = bitable_settings.get("table_id") or DEFAULT_TABLE_ID
            bitable_token = get_bitable_access_token(config_path=args.config)
            sample_options = list_sample_product_options(
                bitable_token,
                app_token=app_token,
                table_id=table_id,
            )
            logger.info(
                f"[飞书] 写入开启 table={table_id} 寄样产品选项={len(sample_options)} 个"
            )
        except Exception as error:
            logger.error(f"[飞书] 初始化失败，将只批准不写表: {error}")
            write_feishu = False

    started_at = datetime.now().isoformat(timespec="seconds")
    items: list[dict[str, Any]] = []
    stop_round = False
    for apply_id in selected_apply_ids:
        if stop_round:
            break
        row = dict(rows_by_apply[apply_id])
        creator_name = str(row.get("creator_name") or "").strip()
        item = {
            "apply_id": apply_id,
            "creator_id": str(row.get("creator_id") or ""),
            "creator_name": creator_name,
            "product_id": str(row.get("product_id") or ""),
            "approve_status": "skipped",
            "approve_error": "",
            "action": "",
            "platform_confirmation_status": "",
            "feishu_relation_status": "",
            "feishu_error": "",
            "feishu_record_id": "",
            "approved_at": "",
        }

        valid, evidence_reason = verify_row_evidence(
            row,
            rule,
            hero_keys=hero_keys,
            allowed_product_ids=selected_allowed,
        )
        if not valid:
            item["approve_error"] = f"自定义证据无效: {evidence_reason}"
            item["action"] = "skipped-evidence-invalid"
            logger.info(f"  [跳过] {creator_name} apply={apply_id} {item['approve_error']}")
            items.append(item)
            continue

        not_hero = _reject_if_not_exact_hero(
            row,
            hero_data,
            allowed_product_ids=selected_allowed,
        )
        if not_hero:
            item["approve_status"] = "skipped"
            item["approve_error"] = not_hero
            item["action"] = "skipped-not-hero"
            logger.info(f"  [跳过] {creator_name} apply={apply_id} 原因={not_hero}")
            items.append(item)
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
        elif product_resolve.get("ok"):
            product_resolve = {
                "ok": True,
                "sku": product_resolve.get("sku"),
                "option": product_resolve.get("sku"),
                "reason": "no-options-skip-option-check",
            }
        if not product_resolve.get("ok"):
            item["approve_status"] = "skipped"
            item["approve_error"] = product_resolve.get("reason")
            item["action"] = "skipped-product-unresolved"
            logger.info(
                f"  [跳过] {creator_name} apply={apply_id} "
                f"原因={product_resolve.get('reason')}"
            )
            items.append(item)
            continue

        if write_feishu and bitable_token:
            try:
                duplicate_resolution = resolve_duplicate_record(
                    bitable_token,
                    creator_handle=creator_name,
                    sample_product=str(product_resolve.get("option")),
                    app_token=app_token,
                    table_id=table_id,
                )
            except FeishuBitableError as error:
                item["approve_status"] = "skipped"
                item["feishu_relation_status"] = "error"
                item["feishu_error"] = f"查重失败: {error}"
                item["action"] = "skipped-dedupe-error"
                logger.info(f"  [跳过] 查重失败 {creator_name}: {error}")
                items.append(item)
                continue
            duplicate_status = str(duplicate_resolution.get("status") or "")
            if duplicate_status in {"ambiguous-match", "invalid-match", "unique-match"}:
                item["approve_status"] = "skipped"
                item["approve_error"] = f"飞书查重拦截: {duplicate_status}"
                item["feishu_relation_status"] = duplicate_status
                item["action"] = f"skipped-duplicate-{duplicate_status}"
                logger.info(f"  [跳过-去重] {creator_name}: {duplicate_status}")
                items.append(item)
                continue
            if duplicate_status != "no-match":
                item["approve_status"] = "skipped"
                item["approve_error"] = f"飞书查重返回未知状态: {duplicate_status or '空'}"
                item["feishu_relation_status"] = "error"
                item["action"] = "skipped-dedupe-unknown"
                logger.info(f"  [跳过-去重] {creator_name}: {item['approve_error']}")
                items.append(item)
                continue

        try:
            pending_status = check_pending_application_api(
                store_id,
                apply_id,
                expected_creator_id=str(row.get("creator_id") or ""),
                expected_product_id=str(row.get("product_id") or ""),
            )
        except Exception as error:
            item["approve_status"] = "skipped"
            item["approve_error"] = f"批准前只读状态预检失败: {error}"
            item["action"] = "skipped-api-preflight-error"
            logger.info(f"  [跳过] {creator_name} 批准前状态预检失败: {error}")
            items.append(item)
            continue
        if (
            not pending_status.get("ok")
            or pending_status.get("state") != "pending-approvable"
        ):
            item["approve_status"] = "skipped"
            item["approve_error"] = (
                "批准前状态不再可批准: "
                f"{pending_status.get('state') or 'unknown'}"
            )
            item["action"] = "skipped-api-preflight-state"
            logger.info(
                f"  [跳过] {creator_name} 批准前状态="
                f"{pending_status.get('state') or 'unknown'}"
            )
            items.append(item)
            continue

        live_sku_check = evaluate_product_sku({
            "product_id": row.get("product_id"),
            "sku_desc": pending_status.get("sku_desc"),
        })
        if live_sku_check is not None:
            item["sku_desc"] = pending_status.get("sku_desc")
            item["sku_check"] = live_sku_check
            if live_sku_check["status"] != "passed":
                item["approve_error"] = live_sku_check["detail"]
                item["action"] = "skipped-b005-sku"
                logger.info(f"  [跳过] {creator_name} apply={apply_id} {item['approve_error']}")
                items.append(item)
                continue

        logger.info(f"  [批准] {creator_name} apply={apply_id}")
        approve_exception: Exception | None = None
        approve_result: dict[str, Any] = {}
        try:
            from lib.zclaw import ensure_store_exec_ready

            ensure_store_exec_ready(store_id, label="批准前")
            approve_result = approve_application_api(
                store_id,
                apply_id,
                expected_creator_id=str(row.get("creator_id") or ""),
                expected_product_id=str(row.get("product_id") or ""),
                preflight_status=pending_status,
            )
            if not approve_result.get("ok"):
                logger.info(f"    批准 API 未接受请求: {approve_result.get('state')}")
        except Exception as error:
            approve_exception = error
            logger.info(f"    批准异常: {error}")

        pending_recheck: dict[str, Any] | None = None
        if not (bool(approve_result.get("ok")) and approve_exception is None):
            try:
                pending_recheck = check_pending_application_api(
                    store_id,
                    apply_id,
                    expected_creator_id=str(row.get("creator_id") or ""),
                    expected_product_id=str(row.get("product_id") or ""),
                )
            except Exception as error:
                logger.info(f"    批准后复查失败: {error}")
        outcome = decide_api_approval_outcome(
            approve_result=approve_result,
            approve_exception=approve_exception,
            pending_recheck=pending_recheck,
        )
        item["approve_status"] = outcome["approve_status"]
        item["action"] = outcome["action"]
        _set_platform_confirmation_status(item, outcome["approve_confirmation"])
        if outcome["approve_status"] in {"failed", "unknown"}:
            item["approve_error"] = (
                str(approve_exception)
                if approve_exception is not None
                else approve_result.get("error") or "批准写结果不确定"
            )
            logger.info(f"    批准未确认成功：停止本轮，禁止自动重试")
            items.append(item)
            stop_round = True
            break

        item["approve_status"] = "approved"
        item["action"] = "approved"
        item["approved_at"] = datetime.now().isoformat(timespec="seconds")
        logger.info(
            f"    批准成功；平台「待发货」约 10 分钟后刷新，列表确认延后"
        )

        if not write_feishu or not bitable_token:
            _set_feishu_relation_status(
                item, "not-requested", legacy_status="skipped-no-write", error=""
            )
            items.append(item)
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
            record_id = str(created.get("record_id") or "").strip()
            if not record_id:
                _set_feishu_relation_status(
                    item,
                    "write-uncertain",
                    legacy_status="error",
                    error="飞书新建响应缺少 record_id；请人工核对后再处理",
                )
                item["action"] = "approved+feishu-uncertain"
                logger.info("    飞书新建响应缺少 record_id，禁止自动重试")
            else:
                _set_feishu_relation_status(
                    item, "created", legacy_status="created", error=""
                )
                item["feishu_record_id"] = record_id
                item["action"] = "approved+feishu"
                logger.info(f"    飞书写入成功 record_id={record_id}")
        except FeishuBitableError as error:
            _set_feishu_relation_status(
                item,
                "write-uncertain",
                legacy_status="error",
                error=f"飞书新建结果不确定：{error}",
            )
            item["action"] = "approved+feishu-uncertain"
            logger.info(f"    飞书新建结果不确定（批准已成功，禁止自动重试）: {error}")
        items.append(item)
        time.sleep(EXECUTE_DELAY_SECONDS)

    # 备份行（reconcile 输入）：预览行合并执行结果，与正式对账字段一致
    backup_rows: list[dict[str, Any]] = []
    items_by_apply = {item["apply_id"]: item for item in items}
    for preview_row in preview_rows:
        apply_id = str(preview_row.get("apply_id") or "")
        item = items_by_apply.get(apply_id)
        merged = dict(preview_row)
        merged["approve_status"] = "export-only"
        merged["approve_forbidden"] = True
        if item is not None:
            merged.update(
                {
                    "approve_status": item["approve_status"],
                    "approve_error": item["approve_error"],
                    "action": item["action"],
                    "approved_at": item["approved_at"],
                    PLATFORM_CONFIRMATION_FIELD: item[
                        "platform_confirmation_status"
                    ],
                    "approve_confirmation": item["platform_confirmation_status"],
                    "feishu_record_id": item["feishu_record_id"],
                    "feishu_error": item["feishu_error"],
                }
            )
            if item["feishu_relation_status"]:
                merged[FEISHU_RELATION_FIELD] = item["feishu_relation_status"]
                merged["feishu_status"] = item["feishu_relation_status"]
        backup_rows.append(merged)
    backup_paths = _write_backup(backup_rows, args.backup_out)

    result_envelope = {
        "envelope_type": "auto_approval_execution",
        "schema_version": SCHEMA_VERSION,
        "execution_id": args.execution_id,
        "preview_id": args.preview_id,
        "store_id": store_id,
        "rule_hash": rule_hash(rule),
        "write_feishu": write_feishu,
        "limit": limit,
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
        "backup_paths": {key: str(path) for key, path in backup_paths.items()},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result_envelope, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    approved_count = sum(1 for item in items if item["approve_status"] == "approved")
    logger.info(
        f"--- 自定义批准完成: 批准成功={approved_count}/{len(items)} "
        f"写飞书={'开' if write_feishu else '关'} ---"
    )
    logger.info(f"执行结果已写入: {args.out}")
    return 0


def _run_reconcile(args: argparse.Namespace) -> int:
    if not args.yes:
        logger.error("核对补写会写飞书（补记录/回填订单号）。确认请加 --yes。")
        return 2
    store_id = args.store_id
    rules_payload = _load_json(args.rules)
    preview = _load_json(args.preview)
    rule = validate_custom_rule(
        rules_payload,
        allowed_product_ids=set(preview.get("allowed_product_ids") or []),
    )
    try:
        verify_preview_envelope(
            preview,
            expected_store_id=store_id,
            expected_rule_hash=rule_hash(rule),
        )
    except AutoApprovalRuleError as error:
        logger.error(f"预览信封校验失败: {error.code} {error}")
        return 2

    hero_data = _load_hero(store_id, args.config)
    rows = _load_export_rows(args.backup)
    if not rows:
        logger.error("备份没有可用行")
        return 1
    limit = args.limit if args.limit > 0 else DEFAULT_EXECUTE_LIMIT
    candidates = _select_confirm_candidates_from_export(rows, force=False)
    feishu_reconcile_rows = _select_feishu_reconcile_rows_from_export(rows)
    order_backfill_rows = _select_order_backfill_rows_from_export(rows, force=False)
    logger.info(f"--- RECONCILE 平台确认目标 {len(candidates)}（limit={limit}）---")
    logger.info(f"--- 订单号回填 {len(order_backfill_rows)}（共享同一 limit）---")
    logger.info(f"--- 飞书恢复补写 {len(feishu_reconcile_rows)}（共享同一 limit）---")
    _run_confirm_pipeline(
        store_id=store_id,
        candidates=candidates,
        hero_data=hero_data,
        write_feishu=bool(args.write_feishu),
        execute_limit=limit,
        config_path=args.config,
        order_backfill_rows=order_backfill_rows,
        reconciliation_rows=rows,
        allowed_product_ids=set(rule.product_ids),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "envelope_type": "auto_approval_reconcile",
                "schema_version": SCHEMA_VERSION,
                "execution_id": args.execution_id,
                "preview_id": args.preview_id,
                "store_id": store_id,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "items": rows,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    logger.info(f"核对结果已写入: {args.out}")
    return 0


def _load_export_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("rows") or data.get("items") or []
    if not isinstance(data, list):
        raise RuntimeError(f"备份不是数组: {path}")
    return [row for row in data if isinstance(row, dict)]


def main() -> int:
    ap = argparse.ArgumentParser(description="自动审批：自定义审核筛查/批准/核对")
    ap.add_argument("--mode", choices=("preview", "execute", "reconcile"), required=True)
    ap.add_argument("--rules", type=Path, required=True, help="自定义规则 JSON 路径")
    ap.add_argument("--store-id", required=True)
    ap.add_argument("--out", type=Path, required=True, help="结果 JSON 输出路径")
    ap.add_argument("--preview", type=Path, default=None, help="预览信封 JSON 路径")
    ap.add_argument("--apply-ids", type=Path, default=None, help="execute：所选申请 ID 数组")
    ap.add_argument("--backup", type=Path, default=None, help="reconcile：执行备份行 JSON")
    ap.add_argument("--backup-out", type=Path, default=None, help="execute：批准前备份前缀")
    ap.add_argument("--execution-id", default="", help="服务端执行批次 ID")
    ap.add_argument("--preview-id", default="", help="服务端预览 ID")
    ap.add_argument("--limit", type=int, default=DEFAULT_EXECUTE_LIMIT)
    ap.add_argument("--write-feishu", type=int, default=0, choices=(0, 1))
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--from-seller-home", action="store_true")
    ap.add_argument("--config", default=None)
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--max-rows", type=int, default=0)
    ap.add_argument("--page-wait", type=float, default=2.0)
    ap.add_argument("--detail-delay", type=float, default=1.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    configure_logging(verbose=bool(args.verbose))

    if args.mode == "preview":
        return _run_preview(args)
    if args.mode == "execute":
        if not args.preview or not args.apply_ids or not args.backup_out:
            logger.error("execute 需要 --preview / --apply-ids / --backup-out")
            return 2
        return _run_execute(args)
    if args.mode == "reconcile":
        if not args.preview or not args.backup:
            logger.error("reconcile 需要 --preview / --backup")
            return 2
        return _run_reconcile(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
