#!/usr/bin/env python3
"""样品申请「待审核」列表的只读 API 适配器。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .page_api import (
    SAMPLE_LIST_ENDPOINT,
    AffiliatePageContext,
    PageApiSchemaError,
    get_affiliate_page_context,
    post_read_json,
)
from .sample_dom import assert_on_pending_list

PENDING_TAB = 10
DEFAULT_PAGE_SIZE = 50

RequestJson = Callable[
    [str, str, dict[str, Any]],
    dict[str, Any],
]


def build_pending_list_request(page: int, page_size: int = DEFAULT_PAGE_SIZE) -> dict[str, Any]:
    return {
        "tab": PENDING_TAB,
        "cur_page": int(page),
        "page_size": int(page_size),
        "search_params": [
            {
                "search_key": 1,
                "search_type": 2,
                "value": "",
            }
        ],
        "order_params": [
            {
                "order_key": 7,
                "order_type": 2,
            }
        ],
    }


def _required_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PageApiSchemaError(f"{label} 应为对象，实际为 {type(value).__name__}")
    return value


def parse_pending_list_payload(
    payload: dict[str, Any],
    *,
    list_href: str,
) -> list[dict[str, Any]]:
    """把 API 聚合项映射为 `sample_dom` 当前使用的标准行。"""
    aggregate_items = payload.get("agg_info")
    if aggregate_items is None and int(payload.get("total_count") or 0) == 0:
        return []
    if not isinstance(aggregate_items, list):
        raise PageApiSchemaError("sample list 响应缺少 agg_info 数组")

    rows: list[dict[str, Any]] = []
    for row_index, aggregate_value in enumerate(aggregate_items):
        aggregate = _required_mapping(aggregate_value, f"agg_info[{row_index}]")
        # 接口字段当前拼写为 apply_deatil；兼容未来修正后的 apply_detail。
        detail = _required_mapping(
            aggregate.get("apply_deatil") or aggregate.get("apply_detail"),
            f"agg_info[{row_index}].apply_deatil",
        )
        group = _required_mapping(
            aggregate.get("apply_group"),
            f"agg_info[{row_index}].apply_group",
        )
        apply_info = detail.get("apply_info")
        if not isinstance(apply_info, dict):
            apply_infos = detail.get("apply_infos")
            if isinstance(apply_infos, list) and apply_infos and isinstance(apply_infos[0], dict):
                apply_info = apply_infos[0]
            else:
                raise PageApiSchemaError(
                    f"agg_info[{row_index}] 缺少主申请 apply_info"
                )
        creator = _required_mapping(
            detail.get("creator_info") or group.get("creator_info"),
            f"agg_info[{row_index}].creator_info",
        )

        apply_id = str(apply_info.get("apply_id") or "").strip()
        creator_name = str(creator.get("name") or "").strip()
        product_id = str(apply_info.get("product_id") or "").strip()
        if not apply_id:
            raise PageApiSchemaError(f"agg_info[{row_index}] 主申请缺少 apply_id")
        if not creator_name:
            raise PageApiSchemaError(f"agg_info[{row_index}] 达人缺少 name")
        if not product_id:
            raise PageApiSchemaError(f"agg_info[{row_index}] 主申请缺少 product_id")

        raw_apply_ids = group.get("apply_ids")
        apply_ids = (
            [str(value) for value in raw_apply_ids if value is not None]
            if isinstance(raw_apply_ids, list)
            else [apply_id]
        )
        if apply_id not in apply_ids:
            apply_ids.insert(0, apply_id)

        rows.append(
            {
                "row_index": row_index,
                "apply_id": apply_id,
                "apply_ids": apply_ids,
                "product_id": product_id,
                "product_title": apply_info.get("product_title") or "",
                "sku_id": str(apply_info.get("sku_id") or ""),
                "sku_desc": apply_info.get("sku_desc") or "",
                "apply_count": group.get("apply_count"),
                "can_be_approved": apply_info.get("can_be_approved"),
                "review_status": apply_info.get("review_status"),
                "creator_id": str(creator.get("creator_id") or ""),
                "tt_uid": str(creator.get("tt_uid") or ""),
                "creator_name": creator_name,
                "nick_name": creator.get("nick_name") or "",
                "follower_num": creator.get("follower_num"),
                "gmv": creator.get("gmv"),
                "item_sold": creator.get("item_sold"),
                "fulfillment_rate": creator.get("fulfillment_rate"),
                "content_video_views": creator.get("content_video_views"),
                "pps_score": creator.get("pps_score"),
                "categories": creator.get("categories") or [],
                "top_follower_gender": creator.get("top_follower_gender") or [],
                "top_follower_ages": creator.get("top_follower_ages") or [],
                "cell_name": "",
                "cell_fulfill": "",
                "cell_video_views": "",
                "cell_gmv": "",
                "cell_units": "",
                "href": list_href,
                "_list_href": list_href,
                "_data_source": "api",
            }
        )
    return rows


def scrape_pending_list_api(
    store_id: str,
    *,
    max_pages: int = 50,
    max_rows: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    ensure_page: bool = True,
    request_json: Callable[..., dict[str, Any]] = post_read_json,
    context: AffiliatePageContext | None = None,
) -> list[dict[str, Any]]:
    """通过页面同源 API 读取待审核列表，不点击分页控件。"""
    if ensure_page:
        assert_on_pending_list(store_id)
    resolved_context = context or get_affiliate_page_context(store_id)

    all_rows: list[dict[str, Any]] = []
    seen_apply_ids: set[str] = set()
    previous_page_signature: tuple[str, ...] | None = None

    for page_number in range(1, max(1, int(max_pages)) + 1):
        request_body = build_pending_list_request(page_number, page_size)
        payload = request_json(
            store_id,
            SAMPLE_LIST_ENDPOINT,
            request_body,
            context=resolved_context,
        )
        page_rows = parse_pending_list_payload(
            payload,
            list_href=resolved_context.href,
        )
        page_signature = tuple(str(row.get("apply_id") or "") for row in page_rows)
        if previous_page_signature is not None and page_signature == previous_page_signature:
            raise PageApiSchemaError(
                f"列表 API 分页未前进，page={page_number} 返回重复 apply_id 集合"
            )
        previous_page_signature = page_signature

        print(
            f"[API扫表] page={page_number} rows={len(page_rows)} "
            f"total={payload.get('total_count')} has_more={bool(payload.get('has_more'))}",
            flush=True,
        )
        for row in page_rows:
            apply_id = str(row.get("apply_id") or "")
            if apply_id in seen_apply_ids:
                continue
            seen_apply_ids.add(apply_id)
            all_rows.append(row)
            if max_rows and len(all_rows) >= max_rows:
                print(f"[API扫表] 达到 max_rows={max_rows}，停止")
                return all_rows

        if not payload.get("has_more"):
            break
        if not page_rows:
            raise PageApiSchemaError(
                f"列表 API 声称 has_more=true，但 page={page_number} 为空"
            )

    print(f"[API扫表] 完成，去重后 {len(all_rows)} 行")
    return all_rows


def locate_pending_application(
    rows: list[dict[str, Any]],
    apply_id: str,
    *,
    expected_creator_id: str = "",
    expected_product_id: str = "",
) -> dict[str, Any]:
    """在标准待审核行中定位申请，并核对批准前不可变业务标识。"""
    normalized_apply_id = str(apply_id or "").strip()
    if not normalized_apply_id:
        raise PageApiSchemaError("批准前状态预检缺少 apply_id")

    matched_row: dict[str, Any] | None = None
    for row in rows:
        row_apply_ids = {
            str(value)
            for value in (row.get("apply_ids") or [])
            if value is not None
        }
        primary_apply_id = str(row.get("apply_id") or "").strip()
        if normalized_apply_id == primary_apply_id or normalized_apply_id in row_apply_ids:
            matched_row = row
            break

    if matched_row is None:
        return {
            "ok": True,
            "state": "not-found-in-pending",
            "apply_id": normalized_apply_id,
        }

    actual_creator_id = str(matched_row.get("creator_id") or "").strip()
    actual_product_id = str(matched_row.get("product_id") or "").strip()
    identity_mismatches: list[str] = []
    if expected_creator_id and actual_creator_id != str(expected_creator_id):
        identity_mismatches.append("creator_id")
    if expected_product_id and actual_product_id != str(expected_product_id):
        identity_mismatches.append("product_id")
    if identity_mismatches:
        return {
            "ok": False,
            "state": "identity-mismatch",
            "apply_id": normalized_apply_id,
            "mismatched_fields": identity_mismatches,
        }

    can_be_approved = matched_row.get("can_be_approved")
    return {
        "ok": True,
        "state": (
            "pending-approvable"
            if can_be_approved is True
            else "pending-not-approvable"
        ),
        "apply_id": normalized_apply_id,
        "can_be_approved": can_be_approved,
        "review_status": matched_row.get("review_status"),
    }


def check_pending_application_api(
    store_id: str,
    apply_id: str,
    *,
    expected_creator_id: str = "",
    expected_product_id: str = "",
) -> dict[str, Any]:
    """通过只读列表 API 即时确认申请仍处于待审核且身份一致。"""
    assert_on_pending_list(store_id)
    context = get_affiliate_page_context(store_id)
    previous_page_signature: tuple[str, ...] | None = None

    for page_number in range(1, 51):
        payload = post_read_json(
            store_id,
            SAMPLE_LIST_ENDPOINT,
            build_pending_list_request(page_number),
            context=context,
        )
        page_rows = parse_pending_list_payload(payload, list_href=context.href)
        page_signature = tuple(str(row.get("apply_id") or "") for row in page_rows)
        if previous_page_signature is not None and page_signature == previous_page_signature:
            raise PageApiSchemaError(
                f"批准前预检分页未前进，page={page_number} 返回重复 apply_id 集合"
            )
        previous_page_signature = page_signature

        page_status = locate_pending_application(
            page_rows,
            apply_id,
            expected_creator_id=expected_creator_id,
            expected_product_id=expected_product_id,
        )
        if page_status.get("state") != "not-found-in-pending":
            page_status["page"] = page_number
            return page_status

        if not payload.get("has_more"):
            return page_status
        if not page_rows:
            raise PageApiSchemaError(
                f"批准前预检 API 声称 has_more=true，但 page={page_number} 为空"
            )

    raise PageApiSchemaError("批准前预检超过 50 页，未能确认完整待审核列表")
