"""Durable job progress and resumable event reads."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from assistant.database.models import Job, JobEvent


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def append_event(
    session_factory,
    job_id: str,
    *,
    level: str,
    event_type: str,
    message: str,
    payload_summary: str | None = None,
) -> int:
    """Append one event with a monotonically increasing per-job sequence."""
    with session_factory() as session:
        current_sequence = session.scalar(
            select(func.max(JobEvent.sequence)).where(JobEvent.job_id == job_id)
        )
        next_sequence = int(current_sequence or 0) + 1
        session.add(
            JobEvent(
                job_id=job_id,
                sequence=next_sequence,
                level=level,
                event_type=event_type,
                message=message,
                payload_summary=payload_summary or "",
            )
        )
        session.commit()
        return next_sequence


def list_events(session_factory, job_id: str, *, after: int = 0) -> list[dict[str, Any]]:
    """Return durable events strictly newer than ``after``."""
    with session_factory() as session:
        events = session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.sequence > after)
            .order_by(JobEvent.sequence)
        ).all()
        return [
            {
                "sequence": event.sequence,
                "level": event.level,
                "event_type": event.event_type,
                "message": event.message,
                "payload_summary": event.payload_summary,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]


def update_progress(
    session_factory,
    job_id: str,
    *,
    current: int,
    total: int,
    message: str,
) -> None:
    """Persist progress and heartbeat together."""
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.progress_current = current
        job.progress_total = total
        job.progress_message = message
        job.heartbeat_at = utc_now()
        session.commit()
    append_event(
        session_factory,
        job_id,
        level="info",
        event_type="job.progress",
        message=message,
    )
