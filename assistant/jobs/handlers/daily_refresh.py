"""Daily read-only refresh for the salesperson workspace."""
from __future__ import annotations

import json

from assistant.jobs.locks import is_cancellation_requested
from assistant.jobs.progress import append_event, update_progress
from assistant.jobs.registry import HandlerFailure, JobCancelled
from assistant.services.followup_service import FollowupService
from assistant.services.shipment_service import ShipmentService
from assistant.services.store_service import StoreService


DAILY_REFRESH_STAGES = 3


def _check_cancelled(job_id: str) -> None:
    if is_cancellation_requested(job_id):
        raise JobCancelled("今日更新已在阶段边界取消")


def run_daily_refresh(job_id: str, session_factory) -> str:
    """Synchronize logistics, then build the local follow-up queue.

    The two existing services remain the source of truth. This handler only
    provides a business-friendly orchestration layer and maps their progress
    into one durable job timeline for the web workspace.
    """
    update_progress(
        session_factory,
        job_id,
        current=0,
        total=DAILY_REFRESH_STAGES,
        message="准备今日更新",
    )
    _check_cancelled(job_id)

    store_result = StoreService(session_factory).resolve_unique_running_store()
    if not store_result.get("ok"):
        raise HandlerFailure(
            "running-not-unique",
            "请只保留一家已登录店铺后，再开始今日更新。",
        )

    def warning(message: str) -> None:
        append_event(
            session_factory,
            job_id,
            level="warning",
            event_type="job.warning",
            message=message,
        )

    def shipment_progress(current: int, total: int, message: str) -> None:
        update_progress(
            session_factory,
            job_id,
            current=1,
            total=DAILY_REFRESH_STAGES,
            message=f"物流同步：{message}",
        )

    update_progress(
        session_factory,
        job_id,
        current=1,
        total=DAILY_REFRESH_STAGES,
        message="正在同步物流和到货状态",
    )
    shipment_result = ShipmentService(
        session_factory,
        warning=warning,
        cancel_check=lambda: _check_cancelled(job_id),
    ).synchronize_shipments(
        store_result["store"],
        job_progress=shipment_progress,
    )
    _check_cancelled(job_id)
    append_event(
        session_factory,
        job_id,
        level="info",
        event_type="job.stage",
        message="物流和到货状态已更新",
    )

    update_progress(
        session_factory,
        job_id,
        current=2,
        total=DAILY_REFRESH_STAGES,
        message="正在生成跟进待办",
    )
    followup_result = FollowupService(
        session_factory,
        warning=warning,
    ).generate()

    update_progress(
        session_factory,
        job_id,
        current=3,
        total=DAILY_REFRESH_STAGES,
        message="今日更新完成",
    )
    return json.dumps(
        {
            "shipment": shipment_result,
            "followup": followup_result,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
