"""Single-thread SQLite worker with fail-closed job dispatch."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from assistant.database.models import Job
from assistant.jobs.locks import (
    clear_cancellation,
    request_cancellation,
)
from assistant.jobs.progress import append_event
from assistant.jobs.registry import HandlerFailure, JobCancelled, get_handler


logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 5.0
TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "interrupted"}
)
_active_job_id: str | None = None
_active_job_lock = threading.Lock()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def mark_stale_jobs_interrupted(session_factory) -> int:
    """Mark abandoned running rows without retrying them."""
    stale_before = utc_now() - timedelta(seconds=60)
    with session_factory() as session:
        stale_jobs = session.scalars(
            select(Job).where(
                Job.status == "running",
                or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < stale_before),
            )
        ).all()
        for job in stale_jobs:
            job.status = "interrupted"
            job.finished_at = utc_now()
            job.error_code = "stale-heartbeat"
            job.error_summary = "任务心跳超时，已标记为中断且不会自动重试。"
        session.commit()
        return len(stale_jobs)


def _claim_next_job(session_factory) -> str | None:
    """Claim one oldest pending row under SQLite BEGIN IMMEDIATE."""
    engine = session_factory.kw.get("bind")
    if engine is None:
        raise RuntimeError("session-factory-missing-bind")
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        running_job_id = connection.scalar(
            select(Job.id).where(Job.status == "running").limit(1)
        )
        if running_job_id is not None:
            connection.commit()
            return None
        job_id = connection.scalar(
            select(Job.id)
            .where(Job.status == "pending")
            .order_by(Job.created_at, Job.id)
            .limit(1)
        )
        if job_id is None:
            connection.commit()
            return None
        claimed_at = utc_now()
        result = connection.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "pending")
            .values(
                status="running",
                started_at=claimed_at,
                heartbeat_at=claimed_at,
                progress_message="任务已领取",
            )
        )
        connection.commit()
        return str(job_id) if result.rowcount == 1 else None


def _finish_job(
    session_factory,
    job_id: str,
    *,
    status: str,
    result_summary: str = "",
    error_code: str = "",
    error_summary: str = "",
) -> None:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = utc_now()
        job.heartbeat_at = utc_now()
        job.result_summary = result_summary
        job.error_code = error_code
        job.error_summary = error_summary
        session.commit()


def _heartbeat_loop(
    session_factory,
    job_id: str,
    stop_event: threading.Event,
    *,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Refresh one running job while its read-only handler is blocked."""
    while not stop_event.wait(interval):
        try:
            with session_factory() as session:
                job = session.get(Job, job_id)
                if job is None or job.status != "running":
                    return
                job.heartbeat_at = utc_now()
                session.commit()
        except Exception:
            logger.exception("job heartbeat failed job_id=%s", job_id)


def worker_loop_once(session_factory) -> str | None:
    """Claim and synchronously execute at most one durable job."""
    job_id = _claim_next_job(session_factory)
    if job_id is None:
        return None
    global _active_job_id
    with _active_job_lock:
        _active_job_id = job_id
    with session_factory() as session:
        job = session.get(Job, job_id)
        job_type = str(job.job_type) if job is not None else ""

    append_event(
        session_factory,
        job_id,
        level="info",
        event_type="job.started",
        message="任务开始执行",
    )
    handler = get_handler(job_type)
    if handler is None:
        _finish_job(
            session_factory,
            job_id,
            status="failed",
            error_code="unknown-job-type",
            error_summary=f"未登记的任务类型：{job_type}",
        )
        append_event(
            session_factory,
            job_id,
            level="error",
            event_type="job.failed",
            message="未知任务类型，已拒绝执行",
        )
        clear_cancellation(job_id)
        with _active_job_lock:
            _active_job_id = None
        return job_id

    heartbeat_stop_event = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(session_factory, job_id, heartbeat_stop_event),
        name=f"job-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        result_summary = handler(job_id, session_factory)
    except JobCancelled:
        _finish_job(session_factory, job_id, status="cancelled")
        append_event(
            session_factory,
            job_id,
            level="warning",
            event_type="job.warning",
            message="任务已在安全检查点取消",
        )
    except HandlerFailure as error:
        _finish_job(
            session_factory,
            job_id,
            status="failed",
            error_code=error.error_code,
            error_summary=error.summary,
        )
        append_event(
            session_factory,
            job_id,
            level="error",
            event_type="job.failed",
            message=error.summary,
        )
    except Exception as error:  # pragma: no cover - defensive boundary
        logger.exception("job handler failed job_id=%s", job_id)
        _finish_job(
            session_factory,
            job_id,
            status="failed",
            error_code="handler-error",
            error_summary=str(error),
        )
        append_event(
            session_factory,
            job_id,
            level="error",
            event_type="job.failed",
            message="任务执行失败",
        )
    else:
        _finish_job(
            session_factory,
            job_id,
            status="succeeded",
            result_summary=result_summary,
        )
        append_event(
            session_factory,
            job_id,
            level="info",
            event_type="job.completed",
            message="任务执行完成",
        )
    finally:
        heartbeat_stop_event.set()
        heartbeat_thread.join(timeout=1.0)
        clear_cancellation(job_id)
        with _active_job_lock:
            _active_job_id = None
    return job_id


@dataclass
class WorkerController:
    stop_event: threading.Event
    thread: threading.Thread

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        with _active_job_lock:
            active_job_id = _active_job_id
        if active_job_id is not None:
            request_cancellation(active_job_id)
        self.thread.join(timeout=timeout)


def start_worker(session_factory) -> WorkerController:
    """Recover stale jobs and start exactly one daemon worker thread."""
    mark_stale_jobs_interrupted(session_factory)
    stop_event = threading.Event()

    def worker_main() -> None:
        while not stop_event.is_set():
            worker_loop_once(session_factory)
            stop_event.wait(0.2)

    thread = threading.Thread(
        target=worker_main,
        name="zn-sample-job-worker",
        daemon=True,
    )
    thread.start()
    return WorkerController(stop_event=stop_event, thread=thread)
