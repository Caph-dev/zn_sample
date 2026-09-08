"""Authenticated durable jobs API and resumable SSE stream."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import update

from assistant.database.models import Job
from assistant.jobs.locks import create_or_get_pending_job, request_cancellation
from assistant.jobs.progress import list_events
from assistant.jobs.registry import WRITE_JOB_TYPES


router = APIRouter()
TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "interrupted"}
)
OPERATOR_JOB_TYPES = frozenset(
    {
        "operator_prepare",
        "operator_screen",
        "operator_pipeline",
        "operator_tracking",
    }
)


def _session_factory(request: Request):
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise HTTPException(status_code=503, detail="job-database-unavailable")
    return session_factory


def _job_payload(job: Job) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "store_id": job.store_id,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "progress_message": job.progress_message,
        "error_code": job.error_code,
        "error_summary": job.error_summary,
        "result_summary": job.result_summary,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.post("/api/jobs/environment-check")
def create_environment_check(request: Request, store_id: str | None = None) -> dict:
    """Create or deduplicate one pending read-only environment check."""
    session_factory = _session_factory(request)
    normalized_store_id = str(store_id).strip() if store_id else None
    job_id, deduplicated = create_or_get_pending_job(
        session_factory,
        job_type="environment_check",
        store_id=normalized_store_id,
    )
    return {"job_id": job_id, "deduplicated": deduplicated}


@router.post("/api/jobs/daily-refresh")
def create_daily_refresh(request: Request) -> dict:
    """Create the salesperson-facing read-only daily update job."""
    session_factory = _session_factory(request)
    job_id, deduplicated = create_or_get_pending_job(
        session_factory,
        job_type="daily_refresh",
        store_id=None,
    )
    return {"job_id": job_id, "deduplicated": deduplicated}


@router.post("/api/jobs/creator-enrich")
def create_creator_enrich(request: Request) -> dict:
    """Create the read-only creator profile enrichment job."""
    session_factory = _session_factory(request)
    job_id, deduplicated = create_or_get_pending_job(
        session_factory,
        job_type="creator_enrich",
        store_id=None,
    )
    return {"job_id": job_id, "deduplicated": deduplicated}


@router.post("/api/jobs/content-thanks-preview")
def create_content_thanks_preview(request: Request) -> dict:
    """Create the dry-run completed-content thanks preview job. Never sends."""
    session_factory = _session_factory(request)
    job_id, deduplicated = create_or_get_pending_job(
        session_factory,
        job_type="content_thanks_preview",
        store_id=None,
    )
    return {"job_id": job_id, "deduplicated": deduplicated}


def _create_operator_job(
    request: Request,
    *,
    job_type: str,
    request_payload: dict | None = None,
) -> dict:
    """Create one allowlisted operator task without accepting shell arguments."""
    if job_type not in OPERATOR_JOB_TYPES:
        raise HTTPException(status_code=404, detail="operator-job-not-found")
    session_factory = _session_factory(request)
    job_id, deduplicated = create_or_get_pending_job(
        session_factory,
        job_type=job_type,
        store_id=None,
        result_summary=json.dumps(
            request_payload or {},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return {"job_id": job_id, "deduplicated": deduplicated}


@router.post("/api/jobs/operator/prepare")
def create_operator_prepare(request: Request) -> dict:
    """Run the same fixed prepare operation as launcher 0."""
    return _create_operator_job(request, job_type="operator_prepare")


@router.post("/api/jobs/operator/screen")
def create_operator_screen(request: Request) -> dict:
    """Run the same read-only formal screening operation as launcher 1."""
    return _create_operator_job(request, job_type="operator_screen")


@router.post("/api/jobs/operator/pipeline")
def create_operator_pipeline(
    request: Request,
    confirmation: str = Form(default=""),
) -> dict:
    """Create launcher 2 only after an explicit typed confirmation."""
    if confirmation.strip().lower() not in {"y", "yes"}:
        raise HTTPException(status_code=400, detail="operator-confirmation-required")
    return _create_operator_job(request, job_type="operator_pipeline")


@router.post("/api/jobs/operator/tracking")
def create_operator_tracking(
    request: Request,
    confirmation: str = Form(default=""),
    force_confirmation: str = Form(default=""),
) -> dict:
    """Create launcher 3 with both write and Beijing-time gates enforced."""
    from lib.operator_launch import beijing_clock, before_four_pm_beijing

    if confirmation.strip().lower() not in {"y", "yes"}:
        raise HTTPException(status_code=400, detail="operator-confirmation-required")
    force_requested = force_confirmation.strip() == "FORCE"
    if before_four_pm_beijing() and not force_requested:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "force-required",
                "clock": beijing_clock(),
                "message": "北京时间 16:00 前默认不运行物流写入和私信任务。",
            },
        )
    return _create_operator_job(
        request,
        job_type="operator_tracking",
        request_payload={"force": force_requested},
    )


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    session_factory = _session_factory(request)
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job-not-found")
        return _job_payload(job)


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    session_factory = _session_factory(request)
    with session_factory() as session:
        cancellation_result = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "pending")
            .values(
                status="cancelled",
                finished_at=datetime.now(timezone.utc),
            )
        )
        if cancellation_result.rowcount == 1:
            session.commit()
            return {"job_id": job_id, "status": "cancelled"}

        session.rollback()
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job-not-found")
        if job.status == "running":
            if job.job_type in OPERATOR_JOB_TYPES or job.job_type in WRITE_JOB_TYPES:
                raise HTTPException(
                    status_code=409,
                    detail="operator-job-not-cancellable",
                )
            request_cancellation(job_id)
            job.progress_message = "已请求取消，将在安全检查点停止"
            session.commit()
            return {"job_id": job_id, "status": "cancellation-requested"}
        raise HTTPException(status_code=409, detail="job-not-cancellable")


@router.get("/api/jobs/{job_id}/events")
def list_job_events(job_id: str, request: Request, after: int = 0) -> dict:
    """一次性 JSON 拉取事件（SSE 之外的补齐通道）。"""
    session_factory = _session_factory(request)
    with session_factory() as session:
        if session.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail="job-not-found")
    return {"events": list_events(session_factory, job_id, after=max(0, after))}


@router.get("/api/jobs/{job_id}/stream")
def stream_job(job_id: str, request: Request, after: int = 0) -> StreamingResponse:
    session_factory = _session_factory(request)
    with session_factory() as session:
        if session.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail="job-not-found")
    last_event_id = request.headers.get("last-event-id", "").strip()
    if last_event_id.isdigit():
        after = max(after, int(last_event_id))

    def event_stream():
        latest_sequence = max(0, after)
        while True:
            events = list_events(session_factory, job_id, after=latest_sequence)
            for event in events:
                latest_sequence = int(event["sequence"])
                safe_data = {
                    "sequence": latest_sequence,
                    "level": event["level"],
                    "event_type": event["event_type"],
                    "message": event["message"],
                    "created_at": event["created_at"],
                }
                yield f"id: {latest_sequence}\n"
                yield (
                    f"event: {event['event_type']}\n"
                    f"data: {json.dumps(safe_data, ensure_ascii=False)}\n\n"
                )
            with session_factory() as session:
                job = session.get(Job, job_id)
                is_terminal = job is None or job.status in TERMINAL_STATUSES
            if is_terminal and not events:
                return
            if not events:
                yield ": keepalive\n\n"
            time.sleep(0.2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
