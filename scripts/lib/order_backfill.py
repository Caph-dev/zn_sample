"""订单号补写：把平台订单号回填到飞书近期缺号的行。

业务默认（服务端与网页共用同一组常量，不接受页面覆盖）：
  - 人员 = 王良希（技术）
  - 记录的系统创建时间在过去 72 小时内
  - 「订单号」为空

查找方式：对每条飞书候选，到商家后台按达人 ID **逐个搜索**，在其各 tab
（待发货 → 已发货 → 处理中 → 已完成 → 待审核）里按「红人ID + 寄样产品」
挑出对应申请并取 ``main_order_id``；列表整表扫描会漏掉已经发货的记录。

只写「订单号」一列：不新建记录、不改「合作状态」/「是否已寄样」，
已有不同订单号不覆盖。写入前后都靠飞书回读核对，幂等可重跑。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .feishu_bitable import (
    DEFAULT_APP_TOKEN,
    DEFAULT_TABLE_ID,
    _field_plain,
    record_created_ms,
    resolve_sample_product_for_row,
    search_records_missing_order_no,
    update_record_order_no,
)
from .tracking_parse import is_order_id

logger = logging.getLogger(__name__)

ORDER_BACKFILL_PERSON = "王良希（技术）"
ORDER_BACKFILL_LOOKBACK_HOURS = 72

# 每个候选的搜索顺序：订单号最可能出现、且最贴近「待发货」的 tab 排前面；
# 某条候选一旦在靠前的 tab 拿到有效订单号就停止继续搜后面的 tab。
PLATFORM_SEARCH_TABS: tuple[tuple[int, str], ...] = (
    (20, "待发货"),
    (30, "已发货"),
    (40, "处理中"),
    (100, "已完成"),
    (10, "待审核"),
)
PLATFORM_TAB_FIELD = "__platform_tab"

# 「订单号」列只认这些结论；其它一律转人工，不自动重试。
ITEM_MATCHED = "matched"
ITEM_NO_PLATFORM_ORDER = "no-platform-order"
ITEM_AMBIGUOUS = "ambiguous"
ITEM_WRITTEN = "written"
ITEM_UNCHANGED = "unchanged"
ITEM_CONFLICT = "conflict"
ITEM_WRITE_UNCERTAIN = "write-uncertain"
ITEM_PLANNED = "planned"
ITEM_SKIPPED_LIMIT = "skipped-limit"


def _candidate_from_record(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields") if isinstance(record, dict) else None
    fields = fields if isinstance(fields, dict) else {}
    created_ms = record_created_ms(record)
    return {
        "record_id": str(record.get("record_id") or "").strip(),
        "creator_handle": _field_plain(fields.get("红人ID")).strip(),
        "sample_product": _field_plain(fields.get("寄样产品")).strip(),
        "cooperation_status": _field_plain(fields.get("合作状态")).strip(),
        "created_ms": created_ms or 0,
        "created_at": (
            datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat()
            if created_ms
            else ""
        ),
    }


def collect_order_backfill_candidates(
    access_token: str,
    *,
    now: datetime | None = None,
    lookback_hours: int = ORDER_BACKFILL_LOOKBACK_HOURS,
    person: str = ORDER_BACKFILL_PERSON,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> list[dict[str, Any]]:
    """按业务窗口读取待补写行；缺身份的行只跳过自己，不丢弃整批。"""
    reference = now or datetime.now(timezone.utc)
    since = reference - timedelta(hours=max(1, int(lookback_hours)))
    records = search_records_missing_order_no(
        access_token,
        since=since,
        person=person,
        app_token=app_token,
        table_id=table_id,
    )
    candidates: list[dict[str, Any]] = []
    for record in records:
        candidate = _candidate_from_record(record)
        if not candidate["record_id"]:
            continue
        if not candidate["creator_handle"] or not candidate["sample_product"]:
            logger.warning(
                "跳过缺身份的行 record=%s 红人ID=%s 寄样产品=%s",
                candidate["record_id"],
                candidate["creator_handle"] or "-",
                candidate["sample_product"] or "-",
            )
            continue
        candidates.append(candidate)
    candidates.sort(key=lambda row: (row["created_ms"], row["record_id"]))
    return candidates


def _row_matches_candidate(
    row: dict[str, Any],
    *,
    creator_handle: str,
    sample_product: str,
    product_id_to_sku: dict[str, str],
    sample_product_options: list[str],
) -> bool:
    """同一达人可能有多笔申请：必须红人ID + 寄样产品都对上才算候选的申请。"""
    row_handle = str(row.get("creator_name") or "").strip().lower()
    if row_handle != (creator_handle or "").strip().lower():
        return False
    resolved = resolve_sample_product_for_row(
        row,
        product_id_to_sku=product_id_to_sku,
        sample_product_options=sample_product_options,
    )
    if not resolved.get("ok"):
        return False
    return str(resolved.get("option") or "") == (sample_product or "").strip()


def select_platform_order(
    rows: list[dict[str, Any]],
    *,
    creator_handle: str,
    sample_product: str,
    product_id_to_sku: dict[str, str],
    sample_product_options: list[str],
) -> dict[str, Any]:
    """从后台搜索结果里挑出该候选的订单号（不写任何远端）。

    - 只有匹配行都拿不到有效订单号（例如仍在待审核）→ ``no-platform-order``；
    - 同一候选出现多个不同订单号 → ``ambiguous``，不挑一个写；
    - 命中则带回 ``order_no`` / ``apply_id`` / ``platform_tab``。
    """
    matched_rows = [
        row
        for row in rows
        if _row_matches_candidate(
            row,
            creator_handle=creator_handle,
            sample_product=sample_product,
            product_id_to_sku=product_id_to_sku,
            sample_product_options=sample_product_options,
        )
    ]
    seen_tabs = [
        str(row.get(PLATFORM_TAB_FIELD) or "") for row in matched_rows
    ]
    order_rows: dict[str, dict[str, Any]] = {}
    for row in matched_rows:
        order_no = str(row.get("main_order_id") or "").strip()
        if order_no and order_no != "0" and is_order_id(order_no):
            order_rows.setdefault(order_no, row)
    if len(order_rows) > 1:
        return {
            "status": ITEM_AMBIGUOUS,
            "order_no": "",
            "apply_id": "",
            "platform_tab": "",
            "platform_tabs": seen_tabs,
            "detail": "同一红人ID+寄样产品在后台出现多个不同订单号，转人工",
        }
    if not order_rows:
        if matched_rows:
            return {
                "status": ITEM_NO_PLATFORM_ORDER,
                "order_no": "",
                "apply_id": "",
                "platform_tab": seen_tabs[0] if seen_tabs else "",
                "platform_tabs": seen_tabs,
                "detail": "后台找到申请但还没有订单号（可能仍在待审核）",
            }
        return {
            "status": ITEM_NO_PLATFORM_ORDER,
            "order_no": "",
            "apply_id": "",
            "platform_tab": "",
            "platform_tabs": [],
            "detail": "后台各 tab 都搜不到匹配的红人ID+寄样产品",
        }
    order_no, row = next(iter(order_rows.items()))
    return {
        "status": ITEM_MATCHED,
        "order_no": order_no,
        "apply_id": str(row.get("apply_id") or ""),
        "platform_tab": str(row.get(PLATFORM_TAB_FIELD) or ""),
        "platform_tabs": seen_tabs,
        "detail": f"后台{str(row.get(PLATFORM_TAB_FIELD) or '列表')}已给出订单号",
    }


def plan_order_backfill(
    candidates: list[dict[str, Any]],
    lookups: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """把候选行与后台查找结果合并成逐行计划（不写任何远端）。"""
    plans: list[dict[str, Any]] = []
    for candidate in candidates:
        lookup = lookups.get(str(candidate.get("record_id"))) or {
            "status": ITEM_NO_PLATFORM_ORDER,
            "order_no": "",
            "apply_id": "",
            "platform_tab": "",
            "platform_tabs": [],
            "detail": "后台各 tab 都搜不到匹配的红人ID+寄样产品",
        }
        plan = dict(candidate)
        plan.update(lookup)
        plans.append(plan)
    return plans


def _report_status_for_write(status: str) -> str:
    mapping = {
        "written": ITEM_WRITTEN,
        "unchanged": ITEM_UNCHANGED,
        "skipped-existing-different": ITEM_CONFLICT,
        "write-uncertain": ITEM_WRITE_UNCERTAIN,
    }
    return mapping.get(status, ITEM_WRITE_UNCERTAIN)


def apply_order_backfill(
    plans: list[dict[str, Any]],
    *,
    access_token: str,
    write_feishu: bool,
    limit: int = 0,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> list[dict[str, Any]]:
    """执行补写并逐行回读；``limit<=0`` 表示不限量。"""
    results: list[dict[str, Any]] = []
    written = 0
    for plan in plans:
        row = dict(plan)
        if row.get("status") != ITEM_MATCHED:
            results.append(row)
            continue
        if not write_feishu:
            row["status"] = ITEM_PLANNED
            results.append(row)
            continue
        if limit and written >= limit:
            row["status"] = ITEM_SKIPPED_LIMIT
            row["detail"] = f"超出本次限量 {limit} 条，未写入"
            results.append(row)
            continue
        written += 1
        try:
            outcome = update_record_order_no(
                access_token,
                str(row.get("record_id") or ""),
                str(row.get("order_no") or ""),
                app_token=app_token,
                table_id=table_id,
            )
        except Exception as error:
            # 传输失败无法区分「写前失败」与「写后未回读」，一律转人工，不自动重试。
            row["status"] = ITEM_WRITE_UNCERTAIN
            row["remote_status"] = "write-error"
            row["current_order"] = ""
            row["detail"] = f"飞书写入异常，结果未知，请人工核对：{error}"
            results.append(row)
            continue
        row["status"] = _report_status_for_write(str(outcome.get("status") or ""))
        row["remote_status"] = str(outcome.get("status") or "")
        row["current_order"] = str(outcome.get("current_order") or "")
        if row["status"] == ITEM_WRITTEN:
            row["detail"] = "已写入并回读一致"
        elif row["status"] == ITEM_UNCHANGED:
            row["detail"] = "飞书已有相同订单号，无需写入"
        elif row["status"] == ITEM_CONFLICT:
            row["detail"] = "飞书已有不同订单号，未覆盖，转人工"
        else:
            row["detail"] = "写入结果无法确认，禁止自动重试，请人工核对"
        results.append(row)
    return results


def build_order_backfill_report(
    *,
    store_id: str,
    person: str,
    lookback_hours: int,
    write_feishu: bool,
    platform_rows: int,
    search_errors: list[str],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "envelope_type": "order_backfill_report",
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "store_id": store_id,
        "person": person,
        "lookback_hours": int(lookback_hours),
        "write_feishu": bool(write_feishu),
        "platform_rows": int(platform_rows),
        "search_errors": [str(error) for error in search_errors][:10],
        "counts": _count_items(items),
        "items": items,
    }


def _count_items(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts
