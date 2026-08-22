"""Read-only dashboard counters."""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from assistant.database.models import FollowupTask, Job, SampleCase, Shipment


router = APIRouter()


def dashboard_summary(session_factory) -> dict:
    with session_factory() as session:
        def count_tasks(stage: str) -> int:
            return int(session.scalar(select(func.count()).select_from(FollowupTask).where(FollowupTask.stage == stage)) or 0)
        return {
            "waiting_delivery": int(session.scalar(select(func.count()).select_from(SampleCase).join(Shipment, Shipment.sample_case_id == SampleCase.id).where(SampleCase.curr_status == 30, Shipment.status_category != "delivered")) or 0),
            "arrival": count_tasks("arrival"),
            "day_3": count_tasks("day_3"),
            "day_7": count_tasks("day_7"),
            "day_10_list": count_tasks("day_10_list"),
            "unfulfilled": count_tasks("unfulfilled"),
            "needs_review": int(session.scalar(select(func.count()).select_from(FollowupTask).where(FollowupTask.requires_manual_confirmation.is_(True))) or 0),
            "failed_jobs": int(session.scalar(select(func.count()).select_from(Job).where(Job.status == "failed")) or 0),
        }


@router.get("/api/dashboard")
def dashboard(request: Request) -> dict:
    return dashboard_summary(request.app.state.session_factory)
