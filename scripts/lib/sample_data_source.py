#!/usr/bin/env python3
"""样品申请列表和筛查详情的数据源编排。"""
from __future__ import annotations

import logging

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .creator_api import fetch_creator_detail_api
from .creator_detail import fetch_detail_for_row
from .sample_api import scrape_pending_list_api
from .sample_dom import scrape_pending_list

logger = logging.getLogger(__name__)

DATA_SOURCE_CHOICES = ("dom", "api", "auto", "shadow")

# 系统级详情接口故障：同一错误会对所有达人复现，逐行回退 DOM 只会白等。
# 已知触发：TikTok 反爬对详情接口返回裸 {"code":100000}（message 常为空），
# 或 "Please remove the plugin and try again"（浏览器插件/环境被判成插件流量）。
SYSTEMIC_DETAIL_FAILURE_LIMIT = 3
SYSTEMIC_DETAIL_ERROR_MARKERS = ("remove the plugin", "code=100000")


def is_systemic_detail_error(reason: Any) -> bool:
    """是否为会逐行复现的系统级详情接口错误（命中后应停止逐行回退）。"""
    text = str(reason or "").lower()
    if not text:
        return False
    return any(marker in text for marker in SYSTEMIC_DETAIL_ERROR_MARKERS)

SHADOW_FIELDS = (
    "apply_id",
    "apply_ids",
    "product_id",
    "product_title",
    "sku_id",
    "sku_desc",
    "can_be_approved",
    "review_status",
    "creator_id",
    "tt_uid",
    "creator_name",
    "nick_name",
    "follower_num",
    "gmv",
    "item_sold",
    "fulfillment_rate",
    "content_video_views",
    "pps_score",
    "categories",
    "top_follower_gender",
    "top_follower_ages",
)


@dataclass
class PendingListResult:
    rows: list[dict[str, Any]]
    source_used: str
    fallback_reason: str = ""
    shadow_report: dict[str, Any] = field(default_factory=dict)


@dataclass
class CreatorDetailResult:
    result: dict[str, Any]
    source_used: str
    fallback_reason: str = ""
    shadow_report: dict[str, Any] = field(default_factory=dict)


DETAIL_COMPARE_FIELDS = (
    "video_gpm_n",
    "live_gpm_n",
    "avg_video_views_n",
    "avg_live_views_n",
    "video_engagement_n",
    "live_engagement_n",
    "est_post_rate_n",
    "aov_detail_n",
)

DETAIL_FIELD_TOLERANCES = {
    "video_gpm_n": 0.05,
    "live_gpm_n": 0.05,
    "avg_video_views_n": 0.5,
    "avg_live_views_n": 0.5,
    "video_engagement_n": 0.01,
    "live_engagement_n": 0.01,
    "est_post_rate_n": 0.01,
    "aov_detail_n": 0.05,
}


def _row_key(row: dict[str, Any]) -> str:
    apply_id = str(row.get("apply_id") or "").strip()
    if apply_id:
        return apply_id
    return "|".join(
        str(row.get(field_name) or "")
        for field_name in ("creator_id", "product_id", "sku_id")
    )


def _comparison_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _masked_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def compare_pending_rows(
    dom_rows: list[dict[str, Any]],
    api_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """按 apply_id 对比标准行，只返回脱敏键和字段名。"""
    dom_by_key = {_row_key(row): row for row in dom_rows}
    api_by_key = {_row_key(row): row for row in api_rows}
    dom_keys = set(dom_by_key)
    api_keys = set(api_by_key)

    missing_in_api = sorted(dom_keys - api_keys)
    missing_in_dom = sorted(api_keys - dom_keys)
    field_mismatches: list[dict[str, Any]] = []
    for key in sorted(dom_keys & api_keys):
        different_fields = [
            field_name
            for field_name in SHADOW_FIELDS
            if _comparison_value(dom_by_key[key].get(field_name))
            != _comparison_value(api_by_key[key].get(field_name))
        ]
        if different_fields:
            field_mismatches.append(
                {
                    "row": _masked_key(key),
                    "fields": different_fields,
                }
            )

    report = {
        "ok": not missing_in_api and not missing_in_dom and not field_mismatches,
        "dom_count": len(dom_rows),
        "api_count": len(api_rows),
        "missing_in_api": [_masked_key(key) for key in missing_in_api],
        "missing_in_dom": [_masked_key(key) for key in missing_in_dom],
        "field_mismatches": field_mismatches,
    }
    return report


def _mark_source(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    return [{**row, "_data_source": source} for row in rows]


def load_pending_rows(
    store_id: str,
    *,
    data_source: str,
    max_pages: int,
    max_rows: int,
    page_wait: float,
) -> PendingListResult:
    if data_source not in DATA_SOURCE_CHOICES:
        raise ValueError(f"未知 data_source={data_source!r}")

    common_arguments = {
        "max_pages": max_pages,
        "max_rows": max_rows,
    }
    if data_source == "dom":
        rows = scrape_pending_list(
            store_id,
            page_wait=page_wait,
            ensure_tab=True,
            **common_arguments,
        )
        return PendingListResult(rows=_mark_source(rows, "dom"), source_used="dom")

    if data_source == "api":
        rows = scrape_pending_list_api(
            store_id,
            ensure_page=True,
            **common_arguments,
        )
        return PendingListResult(rows=rows, source_used="api")

    if data_source == "auto":
        try:
            rows = scrape_pending_list_api(
                store_id,
                ensure_page=True,
                **common_arguments,
            )
            return PendingListResult(rows=rows, source_used="api")
        except Exception as error:
            fallback_reason = f"{type(error).__name__}: {error}"
            logger.info(f"[数据源] API 失败，回退 DOM: {fallback_reason}")
            rows = scrape_pending_list(
                store_id,
                page_wait=page_wait,
                ensure_tab=True,
                **common_arguments,
            )
            marked_rows = _mark_source(rows, "dom-fallback")
            for row in marked_rows:
                row["_fallback_reason"] = fallback_reason
            return PendingListResult(
                rows=marked_rows,
                source_used="dom-fallback",
                fallback_reason=fallback_reason,
            )

    # shadow 始终以 DOM 结果作为后续筛选权威来源。
    api_rows: list[dict[str, Any]] = []
    api_error = ""
    try:
        api_rows = scrape_pending_list_api(
            store_id,
            ensure_page=True,
            **common_arguments,
        )
    except Exception as error:
        api_error = f"{type(error).__name__}: {error}"
        logger.info(f"[shadow] API 读取失败: {api_error}")

    dom_rows = scrape_pending_list(
        store_id,
        page_wait=page_wait,
        ensure_tab=True,
        **common_arguments,
    )
    report = compare_pending_rows(dom_rows, api_rows) if not api_error else {
        "ok": False,
        "dom_count": len(dom_rows),
        "api_count": 0,
        "api_error": api_error,
        "missing_in_api": [],
        "missing_in_dom": [],
        "field_mismatches": [],
    }
    logger.info(
        "[shadow] "
        f"ok={report.get('ok')} dom={report.get('dom_count')} "
        f"api={report.get('api_count')} "
        f"missing_api={len(report.get('missing_in_api') or [])} "
        f"missing_dom={len(report.get('missing_in_dom') or [])} "
        f"field_mismatches={len(report.get('field_mismatches') or [])}")
    marked_rows = _mark_source(dom_rows, "dom-shadow")
    return PendingListResult(
        rows=marked_rows,
        source_used="dom-shadow",
        fallback_reason=api_error,
        shadow_report=report,
    )


def compare_creator_details(
    row: dict[str, Any],
    dom_detail: dict[str, Any],
    api_detail: dict[str, Any],
) -> dict[str, Any]:
    """比较筛查相关数值字段；报告只保留脱敏行标识和字段名。"""
    mismatches: list[dict[str, Any]] = []
    api_only_fields: list[str] = []
    dom_only_fields: list[str] = []
    matched_fields: list[str] = []

    for field_name in DETAIL_COMPARE_FIELDS:
        dom_value = dom_detail.get(field_name)
        api_value = api_detail.get(field_name)
        if dom_value is None and api_value is None:
            continue
        if dom_value is None:
            api_only_fields.append(field_name)
            continue
        if api_value is None:
            dom_only_fields.append(field_name)
            continue
        try:
            absolute_difference = abs(float(dom_value) - float(api_value))
        except (TypeError, ValueError):
            absolute_difference = float("inf")
        tolerance = DETAIL_FIELD_TOLERANCES[field_name]
        if absolute_difference <= tolerance + 1e-9:
            matched_fields.append(field_name)
        else:
            mismatches.append(
                {
                    "field": field_name,
                    "difference": round(absolute_difference, 4),
                    "tolerance": tolerance,
                }
            )

    if mismatches or dom_only_fields:
        comparison_status = "mismatch"
    elif api_only_fields and not matched_fields:
        comparison_status = "api-more-complete"
    elif api_only_fields:
        comparison_status = "matched-with-api-extra"
    elif matched_fields:
        comparison_status = "matched"
    else:
        comparison_status = "no-comparable-metrics"

    row_key = _row_key(row)
    return {
        "ok": not mismatches and not dom_only_fields,
        "comparison_status": comparison_status,
        "row": _masked_key(row_key),
        "matched_fields": matched_fields,
        "mismatches": mismatches,
        "api_only_fields": api_only_fields,
        "dom_only_fields": dom_only_fields,
    }


def _mark_detail_result(
    result: dict[str, Any],
    source: str,
    *,
    fallback_reason: str = "",
) -> dict[str, Any]:
    marked_result = dict(result)
    detail = marked_result.get("detail")
    if isinstance(detail, dict):
        marked_detail = dict(detail)
        marked_detail["_detail_data_source"] = source
        if fallback_reason:
            marked_detail["_detail_fallback_reason"] = fallback_reason
        marked_result["detail"] = marked_detail
    return marked_result


def load_creator_detail(
    store_id: str,
    row: dict[str, Any],
    *,
    data_source: str,
    list_href: str,
    wait: float,
) -> CreatorDetailResult:
    """按列表数据源模式读取详情；shadow 仍以 DOM 为最终权威。"""
    if data_source not in DATA_SOURCE_CHOICES:
        raise ValueError(f"未知 data_source={data_source!r}")

    if data_source == "dom":
        result = fetch_detail_for_row(
            store_id,
            row,
            list_href=list_href,
            prefer_url=True,
            wait=wait,
        )
        return CreatorDetailResult(
            result=_mark_detail_result(result, "dom"),
            source_used="dom",
        )

    if data_source == "api":
        result = fetch_creator_detail_api(store_id, row)
        return CreatorDetailResult(
            result=_mark_detail_result(result, "api"),
            source_used="api",
        )

    if data_source == "auto":
        api_result = fetch_creator_detail_api(store_id, row)
        if api_result.get("ok"):
            return CreatorDetailResult(
                result=_mark_detail_result(api_result, "api"),
                source_used="api",
            )
        fallback_reason = str(api_result.get("error") or "unknown-api-detail-error")
        logger.info(
            f"    [详情数据源] API 传输/结构失败，回退 DOM: {fallback_reason}")
        dom_result = fetch_detail_for_row(
            store_id,
            row,
            list_href=list_href,
            prefer_url=True,
            wait=wait,
        )
        return CreatorDetailResult(
            result=_mark_detail_result(
                dom_result,
                "dom-fallback",
                fallback_reason=fallback_reason,
            ),
            source_used="dom-fallback",
            fallback_reason=fallback_reason,
        )

    api_result = fetch_creator_detail_api(store_id, row)
    dom_result = fetch_detail_for_row(
        store_id,
        row,
        list_href=list_href,
        prefer_url=True,
        wait=wait,
    )
    api_error = "" if api_result.get("ok") else str(api_result.get("error") or "")
    if api_result.get("ok") and dom_result.get("ok"):
        report = compare_creator_details(
            row,
            dom_result.get("detail") or {},
            api_result.get("detail") or {},
        )
    else:
        report = {
            "ok": False,
            "row": _masked_key(_row_key(row)),
            "api_error": api_error,
            "dom_error": "" if dom_result.get("ok") else str(dom_result.get("error") or ""),
            "matched_fields": [],
            "mismatches": [],
            "api_only_fields": [],
            "dom_only_fields": [],
        }
    logger.info(
        f"    [详情shadow] status={report.get('comparison_status')} "
        f"ok={report.get('ok')} "
        f"matched={len(report.get('matched_fields') or [])} "
        f"mismatches={len(report.get('mismatches') or [])} "
        f"api_only={len(report.get('api_only_fields') or [])} "
        f"dom_only={len(report.get('dom_only_fields') or [])}")
    return CreatorDetailResult(
        result=_mark_detail_result(dom_result, "dom-shadow"),
        source_used="dom-shadow",
        fallback_reason=api_error,
        shadow_report=report,
    )
