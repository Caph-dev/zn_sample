"""Normalize TikTok sample-application tab codes into current platform status."""
from __future__ import annotations


PLATFORM_STATUS_PENDING_REVIEW = "pending_review"
PLATFORM_STATUS_READY_TO_SHIP = "ready_to_ship"
PLATFORM_STATUS_SHIPPED = "shipped"
PLATFORM_STATUS_PROCESSING = "processing"
PLATFORM_STATUS_CANCELLED = "cancelled"
PLATFORM_STATUS_COMPLETED = "completed"
PLATFORM_STATUS_UNKNOWN = "unknown"

PLATFORM_STATUS_LABELS = {
    PLATFORM_STATUS_PENDING_REVIEW: "待审核",
    PLATFORM_STATUS_READY_TO_SHIP: "待发货",
    PLATFORM_STATUS_SHIPPED: "已发货",
    PLATFORM_STATUS_PROCESSING: "处理中",
    PLATFORM_STATUS_CANCELLED: "已取消",
    PLATFORM_STATUS_COMPLETED: "已完成",
    PLATFORM_STATUS_UNKNOWN: "未知",
}

PLATFORM_STATUS_BY_CODE = {
    10: PLATFORM_STATUS_PENDING_REVIEW,
    11: PLATFORM_STATUS_PENDING_REVIEW,
    20: PLATFORM_STATUS_READY_TO_SHIP,
    30: PLATFORM_STATUS_SHIPPED,
    40: PLATFORM_STATUS_PROCESSING,
    50: PLATFORM_STATUS_COMPLETED,
    100: PLATFORM_STATUS_COMPLETED,
}

PLATFORM_STATUS_SOURCE_BY_CODE = {
    30: "shipped_tab",
    40: "processing_tab",
    50: "completed_tab",
    100: "completed_tab",
    20: "ready_to_ship_tab",
    10: "pending_tab",
    11: "pending_tab",
}

FOLLOWUP_ELIGIBLE_PLATFORM_STATUSES = frozenset({PLATFORM_STATUS_PROCESSING})


def normalize_platform_status(curr_status: int | None) -> dict[str, str | int]:
    """Map a raw platform tab/status code to the local current-status fields."""
    try:
        raw_code = int(curr_status or 0)
    except (TypeError, ValueError):
        raw_code = 0
    platform_status = PLATFORM_STATUS_BY_CODE.get(raw_code, PLATFORM_STATUS_UNKNOWN)
    return {
        "curr_status": raw_code,
        "platform_status": platform_status,
        "platform_status_label": PLATFORM_STATUS_LABELS[platform_status],
        "platform_status_source": PLATFORM_STATUS_SOURCE_BY_CODE.get(raw_code, ""),
    }


def is_processing_followup_case(*, platform_status: str, stale: bool = False) -> bool:
    """Follow-up calendar only runs on currently observed processing cases."""
    return (not stale) and platform_status in FOLLOWUP_ELIGIBLE_PLATFORM_STATUSES
