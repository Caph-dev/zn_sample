#!/usr/bin/env python3
"""达人详情只读 API 适配器。

TikTok 当前按 profile_type 拆分响应。合并请求会丢失 type 3/5 的独有字段，
因此保持四次小请求，并把结果映射为 creator_detail.py 的标准详情字典。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .page_api import (
    CREATOR_PROFILE_ENDPOINT,
    AffiliatePageContext,
    PageApiSchemaError,
    get_affiliate_page_context,
    post_read_json,
)
from .parse_metrics import parse_count, parse_money

PROFILE_TYPES = (2, 3, 4, 5)


def build_creator_profile_request(
    creator_id: str,
    profile_type: int,
) -> dict[str, Any]:
    normalized_creator_id = str(creator_id or "").strip()
    if not normalized_creator_id:
        raise PageApiSchemaError("达人详情 API 缺少 creator_id")
    if profile_type not in PROFILE_TYPES:
        raise PageApiSchemaError(f"未登记的 profile_type={profile_type}")
    return {
        "creator_oec_id": normalized_creator_id,
        "profile_types": [profile_type],
    }


def _profile_mapping(payload: dict[str, Any], profile_type: int) -> dict[str, Any]:
    profile = payload.get("creator_profile")
    if profile is None and profile_type == 4:
        # 2026-08-14 实测 type 4 成功响应只有 code/message，不带 creator_profile。
        return {}
    if not isinstance(profile, dict):
        raise PageApiSchemaError(
            f"profile_type={profile_type} 响应缺少 creator_profile 对象"
        )
    return profile


def _authorized_value(profile: dict[str, Any], field_name: str) -> Any:
    wrapper = profile.get(field_name)
    if not isinstance(wrapper, dict):
        return None
    if wrapper.get("is_authorized") is False:
        return None
    return wrapper.get("value")


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


def _extract_money_metric(
    profile: dict[str, Any],
    field_name: str,
) -> tuple[str, float | None]:
    value = _authorized_value(profile, field_name)
    if isinstance(value, dict):
        # 普通金额对象：{value: "16.99", format: "$17"}。
        numeric_value = parse_money(value.get("value"))
        if numeric_value is not None:
            display_value = str(value.get("format") or f"${numeric_value:g}")
            return display_value, numeric_value

        # GPM 区间对象。筛选采用区间下限，避免把区间上限当成稳定表现。
        minimum_value = parse_money(value.get("minimal"))
        maximum_value = parse_money(value.get("maximum"))
        selected_value = minimum_value if minimum_value is not None else maximum_value
        display_value = str(
            value.get("minimal_format")
            or value.get("maximum_format")
            or (f"${selected_value:g}" if selected_value is not None else "")
        )
        return display_value, selected_value

    numeric_value = parse_money(value)
    if numeric_value is None:
        return "", None
    return f"${numeric_value:g}", numeric_value


def _extract_count_metric(
    profile: dict[str, Any],
    field_name: str,
) -> tuple[str, float | None]:
    numeric_value = parse_count(_authorized_value(profile, field_name))
    return _format_number(numeric_value), numeric_value


def _extract_basis_point_percent(
    profile: dict[str, Any],
    field_name: str,
) -> tuple[str, float | None]:
    raw_value = _authorized_value(profile, field_name)
    try:
        percent_value = float(raw_value) / 100.0
    except (TypeError, ValueError):
        return "", None
    return f"{percent_value:g}%", percent_value


def parse_creator_profile_payloads(
    payloads_by_type: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """合并 profile 响应并映射为现有详情字段。"""
    missing_types = [
        profile_type
        for profile_type in PROFILE_TYPES
        if profile_type not in payloads_by_type
    ]
    if missing_types:
        raise PageApiSchemaError(f"缺少 profile_types 响应: {missing_types}")

    profiles = {
        profile_type: _profile_mapping(payloads_by_type[profile_type], profile_type)
        for profile_type in PROFILE_TYPES
    }
    core_profile = profiles[2]

    video_gpm, video_gpm_number = _extract_money_metric(
        core_profile,
        "ec_video_gpm",
    )
    live_gpm, live_gpm_number = _extract_money_metric(
        core_profile,
        "ec_live_gpm",
    )
    overall_gpm, overall_gpm_number = _extract_money_metric(core_profile, "gpm")
    avg_video_views, avg_video_views_number = _extract_count_metric(
        core_profile,
        "video_med_view_cnt",
    )
    avg_live_views, avg_live_views_number = _extract_count_metric(
        core_profile,
        "live_med_view_cnt",
    )
    video_engagement, video_engagement_number = _extract_basis_point_percent(
        core_profile,
        "video_engagement",
    )
    live_engagement, live_engagement_number = _extract_basis_point_percent(
        core_profile,
        "live_engagement",
    )
    est_post_rate, est_post_rate_number = _extract_basis_point_percent(
        core_profile,
        "sample_fulfillment_rate",
    )
    revenue_per_buyer, revenue_per_buyer_number = _extract_money_metric(
        core_profile,
        "avg_revenue_per_buyer",
    )

    if video_gpm_number is None and live_gpm_number is None:
        raise PageApiSchemaError("profile_type=2 缺少视频和直播 GPM 核心指标")

    creator_type = ""
    has_video_activity = bool(video_gpm_number is not None and video_gpm_number > 0)
    has_live_activity = bool(live_gpm_number is not None and live_gpm_number > 0)
    if has_video_activity and has_live_activity:
        creator_type = "视频+直播"
    elif has_video_activity:
        creator_type = "视频达人"
    elif has_live_activity:
        creator_type = "直播达人"

    return {
        "bio": "",
        "video_gpm": video_gpm,
        "live_gpm": live_gpm,
        "overall_gpm": overall_gpm,
        "avg_video_views": avg_video_views,
        "avg_live_views": avg_live_views,
        "video_engagement": video_engagement,
        "live_engagement": live_engagement,
        "est_post_rate": est_post_rate,
        "revenue_per_buyer": revenue_per_buyer,
        "creator_type": creator_type,
        "video_gpm_n": video_gpm_number,
        "live_gpm_n": live_gpm_number,
        "overall_gpm_n": overall_gpm_number,
        "avg_video_views_n": avg_video_views_number,
        "avg_live_views_n": avg_live_views_number,
        "video_engagement_n": video_engagement_number,
        "live_engagement_n": live_engagement_number,
        "est_post_rate_n": est_post_rate_number,
        "aov_detail_n": revenue_per_buyer_number,
        "has_cn_video_card": False,
        "has_en_video_card": False,
        "extract_via": "api-profile-types-2-3-4-5",
        "profile_type_field_counts": {
            str(profile_type): len(profile)
            for profile_type, profile in profiles.items()
        },
    }


def fetch_creator_detail_api(
    store_id: str,
    row: dict[str, Any],
    *,
    request_json: Callable[..., dict[str, Any]] = post_read_json,
    context: AffiliatePageContext | None = None,
) -> dict[str, Any]:
    """从页面同源 profile API 拉取详情，不打开达人详情页。"""
    creator_id = str(row.get("creator_id") or "").strip()
    if not creator_id:
        return {"ok": False, "error": "missing-creator-id"}

    try:
        resolved_context = context or get_affiliate_page_context(store_id)
        payloads_by_type: dict[int, dict[str, Any]] = {}
        for profile_type in PROFILE_TYPES:
            payloads_by_type[profile_type] = request_json(
                store_id,
                CREATOR_PROFILE_ENDPOINT,
                build_creator_profile_request(creator_id, profile_type),
                context=resolved_context,
            )
        detail = parse_creator_profile_payloads(payloads_by_type)
    except Exception as error:
        return {
            "ok": False,
            "error": f"{type(error).__name__}: {error}",
        }

    return {
        "ok": True,
        "detail": detail,
        "opened": {"via": "api", "profile_types": list(PROFILE_TYPES)},
    }
