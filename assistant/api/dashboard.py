"""Read-only dashboard counters."""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from assistant.database.models import FollowupTask, Job, SampleCase, Shipment


router = APIRouter()
ACTIVE_TASK_STATUSES = frozenset({"pending", "ready", "needs_review"})
LOGISTICS_EXCEPTION_CATEGORIES = frozenset({"exception", "returned", "lost"})


def dashboard_summary(session_factory) -> dict:
    with session_factory() as session:
        def count_tasks(stage: str, *, processing_only: bool = False) -> int:
            query = (
                select(func.count())
                .select_from(FollowupTask)
                .join(SampleCase, FollowupTask.sample_case_id == SampleCase.id)
                .where(
                    FollowupTask.stage == stage,
                    FollowupTask.status.in_(ACTIVE_TASK_STATUSES),
                )
            )
            if processing_only:
                query = query.where(SampleCase.curr_status == 40)
            return int(session.scalar(query) or 0)

        latest_jobs = {}
        for job_type in ("shipment_sync", "followup_generate"):
            job = session.scalar(
                select(Job)
                .where(Job.job_type == job_type)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            latest_jobs[job_type] = (
                {
                    "status": job.status,
                    "created_at": job.created_at.isoformat(),
                    "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                }
                if job else None
            )
        return {
            "waiting_delivery": int(session.scalar(select(func.count()).select_from(SampleCase).join(Shipment, Shipment.sample_case_id == SampleCase.id).where(SampleCase.curr_status == 30, Shipment.status_category != "delivered")) or 0),
            "arrival": count_tasks("arrival"),
            "day_3": count_tasks("day_3"),
            "day_7": count_tasks("day_7"),
            "day_10_list": count_tasks("day_10_list", processing_only=True),
            "unfulfilled": count_tasks("unfulfilled", processing_only=True),
            "needs_review": int(session.scalar(select(func.count()).select_from(FollowupTask).where(FollowupTask.requires_manual_confirmation.is_(True), FollowupTask.status.in_(ACTIVE_TASK_STATUSES))) or 0),
            "logistics_exceptions": int(session.scalar(select(func.count()).select_from(Shipment).where((Shipment.status_category.in_(LOGISTICS_EXCEPTION_CATEGORIES)) | Shipment.needs_delivery_time_confirmation.is_(True))) or 0),
            "failed_jobs": int(session.scalar(select(func.count()).select_from(Job).where(Job.status == "failed")) or 0),
            "latest_jobs": latest_jobs,
        }


@router.get("/api/dashboard")
def dashboard(request: Request) -> dict:
    return dashboard_summary(request.app.state.session_factory)
