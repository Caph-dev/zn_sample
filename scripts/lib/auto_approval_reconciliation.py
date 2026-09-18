"""Read-back verification for custom approvals; this module never writes remotely."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .app_config import load_bitable_settings
from .feishu_bitable import (
    DEFAULT_APP_TOKEN, DEFAULT_TABLE_ID, DEFAULT_VIEW_ID,
    _field_plain, build_product_id_to_sku_map, get_bitable_access_token,
    get_record_fields, list_sample_product_options, resolve_duplicate_record,
    resolve_sample_product_for_row,
)
from .sample_write_api import confirm_application_approved_api


def inspect_reconciliation_rows(
    rows: list[dict[str, Any]], *, store_id: str,
    hero_data: dict[str, Any], config_path: str | None,
) -> list[dict[str, Any]]:
    """Verify platform identity, relation key and order number using fresh reads.

    Local 'created' or record_id checkpoints are never treated as read-back
    evidence. An uncertain previous create may be linked if found uniquely,
    but an empty search must not authorize another create.
    """
    from screen_sample_requests import _looks_like_tiktok_order_no

    initialization_error = ""
    try:
        settings = load_bitable_settings(
            config_path=config_path, default_app_token=DEFAULT_APP_TOKEN,
            default_table_id=DEFAULT_TABLE_ID, default_view_id=DEFAULT_VIEW_ID,
        )
        access_token = get_bitable_access_token(config_path=config_path)
        target = {
            "app_token": settings.get("app_token") or DEFAULT_APP_TOKEN,
            "table_id": settings.get("table_id") or DEFAULT_TABLE_ID,
        }
        options = list_sample_product_options(access_token, **target)
        product_mapping = build_product_id_to_sku_map(hero_data)
    except Exception as error:
        initialization_error = f"飞书查询初始化失败：{error}"

    results: list[dict[str, Any]] = []
    for row in rows:
        result: dict[str, Any] = {
            "apply_id": str(row.get("apply_id") or ""),
            "creator_name": str(row.get("creator_name") or ""),
            "product_id": str(row.get("product_id") or ""),
            "platform_status": "unknown", "relation_status": "unknown",
            "order_status": "unknown", "status": "needs_review",
            "detail": "", "record_id": "", "order_no": "", "can_repair": False,
        }
        results.append(result)
        if row.get("approve_status") not in {"approved", "unknown"}:
            result.update(platform_status="not-approved", detail="本批次没有批准成功或结果未知的执行证据，不能补写。")
            continue
        if not all(row.get(field) for field in ("apply_id", "creator_id", "creator_name", "product_id")):
            result["detail"] = "执行备份缺少申请、达人或商品身份，不能自动核对。"
            continue
        try:
            platform = confirm_application_approved_api(
                store_id, result["apply_id"],
                expected_creator_id=str(row["creator_id"]),
                expected_product_id=result["product_id"], max_attempts=1,
            )
            ready_row = platform.get("ready_to_ship") or {}
            confirmed = (
                platform.get("ok") is True and platform.get("state") == "approved"
                and ready_row.get("state") != "identity-mismatch"
                and ready_row.get("curr_status") == 20
            )
            result["platform_status"] = "confirmed" if confirmed else str(platform.get("state") or "unknown")
            result["order_no"] = _looks_like_tiktok_order_no(ready_row.get("main_order_id")) if confirmed else ""
        except Exception as error:
            result.update(platform_status="error", detail=f"平台查询失败：{error}")
        if initialization_error:
            result.update(relation_status="error", detail=initialization_error)
            continue
        try:
            product = resolve_sample_product_for_row(
                row, product_id_to_sku=product_mapping, sample_product_options=options,
            )
            if not product.get("ok"):
                result.update(relation_status="conflict", detail="商品无法唯一映射到飞书寄样产品，需人工核对。")
                continue
            sample_product = str(product["option"])
            frozen_option = str(row.get("sample_product_option") or "")
            if frozen_option and frozen_option != sample_product:
                result.update(relation_status="conflict", detail="当前商品映射与执行备份不一致，禁止自动补写。")
                continue
            result["sample_product_option"] = sample_product
            result["resolved_sku"] = str(product.get("sku") or "")
            duplicate = resolve_duplicate_record(
                access_token, creator_handle=result["creator_name"],
                sample_product=sample_product, **target,
            )
            if duplicate.get("status") == "no-match":
                previous_status = str(row.get("feishu_relation_status") or row.get("feishu_status") or "")
                if row.get("feishu_record_id") or previous_status in {
                    "created", "linked-existing", "write-uncertain", "ambiguous-match", "invalid-match",
                }:
                    result.update(relation_status="unknown", detail="历史记录曾写入、结果不确定或有冲突；本次未找到，不自动重建。")
                    continue
                result.update(relation_status="missing", order_status="missing" if result["order_no"] else "unavailable")
            elif duplicate.get("status") == "unique-match":
                record_id = str((duplicate.get("record") or {}).get("record_id") or "")
                if row.get("feishu_record_id") and row["feishu_record_id"] != record_id:
                    result.update(relation_status="conflict", detail="唯一匹配记录与原执行记录 ID 不一致，禁止自动覆盖。")
                    continue
                fields = get_record_fields(access_token, record_id, **target)
                if (
                    _field_plain(fields.get("红人ID")).strip() != result["creator_name"]
                    or _field_plain(fields.get("寄样产品")).strip() != sample_product
                ):
                    result.update(relation_status="conflict", detail="回读记录主键与达人、寄样产品不一致。")
                    continue
                result.update(relation_status="verified", record_id=record_id)
                current_order = _field_plain(fields.get("订单号")).strip()
                if not result["order_no"]:
                    result["order_status"] = "unavailable"
                elif current_order == result["order_no"]:
                    result["order_status"] = "verified"
                elif current_order:
                    result.update(order_status="conflict", detail="飞书已有不同订单号，禁止覆盖。")
                elif row.get("order_backfill_status") == "write-uncertain" or row.get("feishu_order_status") == "write-uncertain":
                    result.update(order_status="unknown", detail="上次订单号写入结果未知，只核对、不自动重试。")
                else:
                    result["order_status"] = "missing"
            else:
                result.update(relation_status="conflict", detail=f"飞书查重结果 {duplicate.get('status') or '未知'}；不新建、不猜记录。")
                continue
        except Exception as error:
            result.update(relation_status="error", detail=f"飞书查询失败，不视为缺失：{error}")
            continue
        if result["platform_status"] != "confirmed":
            result["detail"] = result["detail"] or "尚未在待发货确认；可能平台尚未刷新或已进入后续阶段，不能据此重批或补写。"
        elif result["relation_status"] == "verified" and result["order_status"] == "verified":
            result.update(status="verified", detail="已回读确认达人关系主键及订单号一致。")
        elif result["relation_status"] == "missing" or result["order_status"] == "missing":
            result.update(status="missing", can_repair=True, detail="已确认缺失，可限量补写；执行前会重新核对。")
        elif not result["detail"]:
            result["detail"] = "达人关系已找到，但平台未提供可核对的订单号；留待后续物流核对。"
    return results


def build_reconciliation_report(
    *, execution_id: str, store_id: str, write_feishu: bool,
    rows: list[dict[str, Any]], items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "envelope_type": "auto_approval_reconcile", "schema_version": 1,
        "execution_id": execution_id, "store_id": store_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "write_feishu": write_feishu, "items": items, "rows": rows,
    }
