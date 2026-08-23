"""Cancellation flags and Zinao request exclusion helpers."""
from __future__ import annotations

import threading
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from assistant.database.models import Job, Store
from assistant.security.csrf import is_local_host
from assistant.jobs.registry import ZINIAO_JOB_TYPES


_cancelled_job_ids: set[str] = set()
_cancellation_lock = threading.Lock()
_job_creation_lock = threading.Lock()


def request_cancellation(job_id: str) -> None:
    with _cancellation_lock:
        _cancelled_job_ids.add(job_id)


def is_cancellation_requested(job_id: str) -> bool:
    with _cancellation_lock:
        return job_id in _cancelled_job_ids


def clear_cancellation(job_id: str) -> None:
    with _cancellation_lock:
        _cancelled_job_ids.discard(job_id)


def create_or_get_pending_job(
    session_factory,
    *,
    job_type: str,
    store_id: str | None,
    result_summary: str = "",
) -> tuple[str, bool]:
    """Deduplicate local job creation across concurrent request threads."""
    with _job_creation_lock:
        with session_factory() as session:
            existing_id = session.scalar(
                select(Job.id)
                .where(
                    Job.job_type == job_type,
                    Job.store_id == store_id,
                    Job.status.in_(("pending", "running")),
                )
                .order_by(Job.created_at)
                .limit(1)
            )
            if existing_id is not None:
                return str(existing_id), True
            job_id = str(uuid.uuid4())
            session.add(
                Job(
                    id=job_id,
                    job_type=job_type,
                    store_id=store_id,
                    status="pending",
                    requested_by="local-session",
                    result_summary=result_summary,
                )
            )
            session.commit()
            return job_id, False


def has_running_ziniao_job(session_factory) -> bool:
    with session_factory() as session:
        running_job_id = session.scalar(
            select(Job.id)
            .where(Job.status == "running", Job.job_type.in_(ZINIAO_JOB_TYPES))
            .limit(1)
        )
        return running_job_id is not None


def cached_store_busy_response(session_factory) -> dict:
    with session_factory() as session:
        stores = session.scalars(select(Store).where(Store.enabled.is_(True))).all()
        safe_stores = [
            {"storeId": store.ziniao_store_id, "storeName": store.store_name}
            for store in stores
        ]
    return {"ok": False, "error": "ziniao-busy", "stores": safe_stores}


def request_safe_store_summary(session_factory) -> dict:
    """Use cached stores while the worker owns Zinao; otherwise read live."""
    if has_running_ziniao_job(session_factory):
        return cached_store_busy_response(session_factory)
    from assistant.api.stores import running_store_summary

    return running_store_summary()


def install_ziniao_busy_guard(application, session_factory) -> None:
    """Prevent request-thread Zinao reads while the worker owns the channel."""

    @application.middleware("http")
    async def guard_stores_during_ziniao_jobs(request: Request, call_next):
        is_stores_get = request.method == "GET" and request.url.path == "/api/stores"
        if not is_stores_get or not is_local_host(request.headers.get("host", "")):
            return await call_next(request)
        if has_running_ziniao_job(session_factory):
            return JSONResponse(cached_store_busy_response(session_factory))
        return await call_next(request)
