"""Store-page lock shared by the send script and the console preview.

Only one operator may drive the Ziniao page at a time: approvals, tracking
sync, enrichment and a real follow-up send all click around the same window.
The console preview opens a conversation too, so it must respect the same
lock as the script that actually sends.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from assistant.database.models import Job

STORE_BUSY_ERROR = "store-busy"

_EXTRA_BLOCKING_JOB_TYPES = frozenset({"followup_generate"})


def blocking_job_types() -> tuple[str, ...]:
    """Job types that occupy the store page (lazy import avoids a cycle)."""
    from assistant.jobs.registry import ZINIAO_JOB_TYPES

    return tuple(ZINIAO_JOB_TYPES | _EXTRA_BLOCKING_JOB_TYPES)


def blocking_jobs(session_factory, *, ignore_job_id: str = "") -> list[dict[str, Any]]:
    """Return pending/running jobs that occupy the store page."""
    with session_factory() as session:
        jobs = session.scalars(
            select(Job)
            .where(
                Job.status.in_(("pending", "running")),
                Job.job_type.in_(blocking_job_types()),
                Job.id != str(ignore_job_id or ""),
            )
            .order_by(Job.created_at.desc())
        ).all()
        return [
            {"id": job.id, "job_type": job.job_type, "status": job.status}
            for job in jobs
        ]
