"""Calendar and mutation planning for unpublished-content follow-ups."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from scripts.lib.feishu_bitable import (
    COOPERATION_STATUS_COMPLETED,
    COOPERATION_STATUS_UNPUBLISHED,
)

from .timeutil import beijing_date, beijing_now


FEISHU_UNFULFILLED_COOPERATION_STATUS = COOPERATION_STATUS_UNPUBLISHED
FEISHU_COMPLETED_COOPERATION_STATUS = COOPERATION_STATUS_COMPLETED
CONFIRM_DELIVERY_TIME_STAGE = "confirm_delivery_time"
CONTENT_FOUND_STAGE = "content_found"

FOLLOWUP_STAGES = frozenset(
    {"arrival", "day_3", "day_7", "day_10_list", "unfulfilled"}
)
ACTIVE_TASK_STATUSES = frozenset({"pending", "needs_review"})
MUTABLE_TASK_STATUSES = ACTIVE_TASK_STATUSES
FOLLOWUP_TASK_STATUSES = frozenset(
    {"pending", "needs_review", "skipped", "suppressed"}
)

ACTION_KIND_SEND_MESSAGE = "send_message"
ACTION_KIND_LIST_ONLY = "list_only"
ACTION_KIND_CONFIRM_DELIVERY_DATE = "confirm_delivery_date"
ACTION_KIND_MARK_UNFULFILLED = "mark_unfulfilled"
ACTION_KIND_ACKNOWLEDGE_CONTENT = "acknowledge_content"
COMPLETED_LOCAL_RESULTS = frozenset({"marked-sent", "listed"})

ACTION_KIND_BY_STAGE = {
    "arrival": ACTION_KIND_SEND_MESSAGE,
    "day_3": ACTION_KIND_SEND_MESSAGE,
    "day_7": ACTION_KIND_SEND_MESSAGE,
    "day_10_list": ACTION_KIND_LIST_ONLY,
    "unfulfilled": ACTION_KIND_MARK_UNFULFILLED,
    CONFIRM_DELIVERY_TIME_STAGE: ACTION_KIND_CONFIRM_DELIVERY_DATE,
    CONTENT_FOUND_STAGE: ACTION_KIND_ACKNOWLEDGE_CONTENT,
}


def action_kind_for_stage(stage: str) -> str:
    return ACTION_KIND_BY_STAGE.get(stage, "")


def followup_task_completed(task: dict) -> bool:
    """Local completion: marked sent or handed to ops. Not a platform send."""
    if task.get("sent_at"):
        return True
    return str(task.get("send_result") or "") in COMPLETED_LOCAL_RESULTS


def days_since_delivery(delivered_at: datetime, today: date | None = None) -> int:
    """Return Beijing natural days elapsed since the confirmed delivery time."""
    delivery_date = beijing_date(delivered_at)
    effective_today = today if today is not None else beijing_date(beijing_now())
    return (effective_today - delivery_date).days


def latest_due_unpublished_stage(days: int) -> tuple[str, int] | None:
    """Return the latest single follow-up stage due for elapsed ``days``."""
    if days < 0:
        return None
    if days < 3:
        return ("arrival", 0)
    if days < 7:
        return ("day_3", 3)
    if days < 10:
        return ("day_7", 7)
    if days < 15:
        return ("day_10_list", 10)
    return ("unfulfilled", 15)


def _as_date(scheduled_for: str | date | None) -> date | None:
    if scheduled_for is None:
        return None
    if isinstance(scheduled_for, datetime):
        return scheduled_for.date()
    if isinstance(scheduled_for, date):
        return scheduled_for
    return date.fromisoformat(scheduled_for)


def plan_followup_mutation(
    *,
    existing_unpublished: list[dict],
    days: int,
    has_confirmed_content: bool,
    today: date | None = None,
) -> dict:
    """Plan creation and suppression without performing persistence or I/O.

    ``today`` lets callers use one frozen natural date for an entire generate
    operation. Direct callers keep the current Beijing-date default.
    """
    suppressions: list[dict] = []

    if has_confirmed_content:
        for existing_task in existing_unpublished:
            if followup_task_completed(existing_task):
                continue
            if (
                existing_task["stage"] in FOLLOWUP_STAGES
                and existing_task["status"] in MUTABLE_TASK_STATUSES
            ):
                suppressions.append(
                    {
                        "stage": existing_task["stage"],
                        "scheduled_for": _as_date(existing_task["scheduled_for"]),
                        "reason": "content_confirmed",
                    }
                )
        return {"create": [], "suppress": suppressions}

    latest_stage = latest_due_unpublished_stage(days)
    if latest_stage is None:
        return {"create": [], "suppress": []}

    stage, offset_days = latest_stage
    effective_today = today if today is not None else beijing_date(beijing_now())
    delivery_date = effective_today - timedelta(days=days)
    scheduled_for = delivery_date + timedelta(days=offset_days)
    target_key = (stage, scheduled_for)
    existing_keys = {
        (existing_task["stage"], _as_date(existing_task["scheduled_for"]))
        for existing_task in existing_unpublished
    }

    creations = []
    if target_key not in existing_keys:
        creations.append(
            {
                "stage": stage,
                "scheduled_for": scheduled_for,
                "status": "pending",
                "action_kind": action_kind_for_stage(stage),
            }
        )

    for existing_task in existing_unpublished:
        existing_key = (
            existing_task["stage"],
            _as_date(existing_task["scheduled_for"]),
        )
        if followup_task_completed(existing_task):
            continue
        if (
            existing_task["stage"] in FOLLOWUP_STAGES
            and existing_task["status"] in MUTABLE_TASK_STATUSES
            and existing_key != target_key
        ):
            suppressions.append(
                {
                    "stage": existing_task["stage"],
                    "scheduled_for": existing_key[1],
                    "reason": "superseded_by_later_stage",
                }
            )

    return {"create": creations, "suppress": suppressions}
