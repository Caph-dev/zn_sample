"""Human-readable labels for follow-up stages, actions, and local statuses."""
from __future__ import annotations

from datetime import datetime


EMPTY_STATUS_DISPLAY = "-"
MARKED_SENT_RESULT = "marked-sent"
LISTED_RESULT = "listed"
COMPLETED_LOCAL_RESULTS = frozenset({MARKED_SENT_RESULT, LISTED_RESULT})
SUPERSEDED_REASON = "superseded_by_later_stage"

FOLLOWUP_STATUS_LABELS = {
    "pending": "待处理",
    "needs_review": "需人工确认",
    "skipped": "已跳过",
    "suppressed": "不再跟进",
}

FOLLOWUP_STAGE_LABELS = {
    "arrival": "到货当天",
    "day_3": "到货后第 3 天",
    "day_7": "到货后第 7 天",
    "day_10_list": "到货后第 10 天",
    "unfulfilled": "到货后第 15 天未履约",
    "confirm_delivery_time": "待确认送达日",
    "content_found": "达人已出内容",
}

FOLLOWUP_ACTION_LABELS = {
    "send_message": "待发跟进私信",
    "list_only": "待出名单给业务",
    "confirm_delivery_date": "确认实际送达日",
    "mark_unfulfilled": "标记未履约",
    "acknowledge_content": "发送内容感谢",
}

FOLLOWUP_ACTION_COMPLETED_LABELS = {
    "send_message": "已发跟进私信",
    "list_only": "已出名单给业务",
}

CREATOR_TYPE_LABELS = {
    "video": "视频达人",
    "live": "直播达人",
    "both": "视频+直播达人",
    "unknown": "类型未知",
}

REVIEW_REASON_LABELS = {
    "ambiguous_creator_type": "同时是视频和直播达人，跟进话术按视频达人处理",
    "missing_creator_type": "达人类型未知，请先选定按视频还是按直播跟进",
    "missing_delivery_time": "请确认实际送达日期；预计送达时间不能当作到货日",
    "missing_template": "当前动作没有可用话术",
    "missing_language": "语言无法可靠确认，请先选定英语或西班牙语",
    "low_confidence_language": "语言置信度不足，请先选定英语或西班牙语",
}

SUPPRESSED_REASON_LABELS = {
    SUPERSEDED_REASON: "已由新阶段取代",
    "delivery_time_known": "送达日已确认",
    "content_confirmed": "达人已出内容",
    "platform_completed": "平台已完成",
    "platform_cancelled": "平台已取消",
}

FOLLOWUP_STATUS_TONES = {
    "pending": "neutral",
    "needs_review": "orange",
    "skipped": "neutral",
    "suppressed": "neutral",
}

FOLLOWUP_STAGE_TONES = {
    "arrival": "teal",
    "day_3": "blue",
    "day_7": "purple",
    "day_10_list": "orange",
    "unfulfilled": "red",
    "confirm_delivery_time": "pink",
    "content_found": "success",
}


def followup_action_completed(
    *,
    sent_at: datetime | None = None,
    send_result: str = "",
) -> bool:
    return sent_at is not None or send_result in COMPLETED_LOCAL_RESULTS


def followup_action_display(
    action_kind: str,
    *,
    sent_at: datetime | None = None,
    send_result: str = "",
) -> str:
    if followup_action_completed(sent_at=sent_at, send_result=send_result):
        completed_label = FOLLOWUP_ACTION_COMPLETED_LABELS.get(action_kind)
        if completed_label:
            return completed_label
    return FOLLOWUP_ACTION_LABELS.get(action_kind, action_kind or "")


def followup_action_tone(
    action_kind: str,
    *,
    sent_at: datetime | None = None,
    send_result: str = "",
) -> str:
    if followup_action_completed(sent_at=sent_at, send_result=send_result):
        if action_kind in FOLLOWUP_ACTION_COMPLETED_LABELS:
            return "success"
    if action_kind in {"send_message", "list_only", "mark_unfulfilled"}:
        return "warning"
    if action_kind == "acknowledge_content":
        return "success"
    return "neutral"


def followup_status_label(status: str, *, suppressed_reason: str = "") -> str:
    if status == "suppressed":
        return SUPPRESSED_REASON_LABELS.get(suppressed_reason, "不再跟进")
    return FOLLOWUP_STATUS_LABELS.get(status, status)


def followup_status_display(status: str, *, suppressed_reason: str = "") -> str:
    if status == "pending":
        return EMPTY_STATUS_DISPLAY
    if status == "suppressed" and suppressed_reason == SUPERSEDED_REASON:
        return EMPTY_STATUS_DISPLAY
    return followup_status_label(status, suppressed_reason=suppressed_reason)


def followup_review_label(review_reason: str) -> str:
    return REVIEW_REASON_LABELS.get(review_reason, review_reason)
