"""Human-readable labels for follow-up stages, actions, and local statuses."""
from __future__ import annotations

from datetime import datetime


EMPTY_STATUS_DISPLAY = "-"
MARKED_SENT_RESULT = "marked-sent"
PLATFORM_SENT_RESULT = "platform-sent"
SEND_UNKNOWN_RESULT = "send-unknown"
SENDING_RESULT = "sending"
LISTED_RESULT = "listed"
COMPLETED_RESULTS = frozenset(
    {MARKED_SENT_RESULT, PLATFORM_SENT_RESULT, LISTED_RESULT}
)
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

FOLLOWUP_STAGE_LIST_ORDER = (
    "unfulfilled",
    "day_10_list",
    "day_7",
    "day_3",
    "arrival",
    "confirm_delivery_time",
    "content_found",
)

FOLLOWUP_LANGUAGE_LABELS = {
    "en": "英语",
    "es": "西班牙语",
}

FOLLOWUP_PLATFORM_STATUS_FILTER_LABELS = {
    "processing": "处理中",
    "shipped": "已发货",
    "completed": "已完成",
    "cancelled": "已取消",
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

FOLLOWUP_SEND_RESULT_LABELS = {
    PLATFORM_SENT_RESULT: "平台已确认发送",
    MARKED_SENT_RESULT: "本地人工标记",
    SEND_UNKNOWN_RESULT: "发送结果未确认",
    SENDING_RESULT: "发送中",
    LISTED_RESULT: "已出名单给业务",
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
    "send_unknown_needs_review": "发送结果未确认；请先人工核对会话，勿自动重发",
    "send_interrupted": "上次发送未完成（进程中断）；请先人工核对会话，勿自动重发",
    "image_send_failed": "配图发送失败；请人工在会话里补发图片，勿重发话术",
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
    return sent_at is not None or send_result in COMPLETED_RESULTS


def followup_action_display(
    action_kind: str,
    *,
    sent_at: datetime | None = None,
    send_result: str = "",
) -> str:
    if followup_action_completed(sent_at=sent_at, send_result=send_result):
        if action_kind == "send_message":
            if send_result == PLATFORM_SENT_RESULT:
                return "已发跟进私信（平台确认）"
            if send_result == MARKED_SENT_RESULT:
                return "已发跟进私信（本地标记）"
        completed_label = FOLLOWUP_ACTION_COMPLETED_LABELS.get(action_kind)
        if completed_label:
            return completed_label
    return FOLLOWUP_ACTION_LABELS.get(action_kind, action_kind or "")


def followup_send_result_label(send_result: str) -> str:
    return FOLLOWUP_SEND_RESULT_LABELS.get(send_result, send_result or "")


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


def followup_stage_list_rank(stage: str) -> int:
    try:
        return FOLLOWUP_STAGE_LIST_ORDER.index(stage)
    except ValueError:
        return len(FOLLOWUP_STAGE_LIST_ORDER)


def followup_language_label(language: str) -> str:
    return FOLLOWUP_LANGUAGE_LABELS.get(language, language or "")
