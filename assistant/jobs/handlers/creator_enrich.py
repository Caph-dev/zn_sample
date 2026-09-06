"""只读达人资料补齐任务：填充跟进池缺失的类型与语言。"""
from __future__ import annotations

import json

from assistant.jobs.locks import is_cancellation_requested
from assistant.jobs.progress import append_event, update_progress
from assistant.jobs.registry import JobCancelled
from assistant.services.creator_enrich_service import CreatorEnrichService
from assistant.services.store_service import StoreService


def resolve_readonly_store(session_factory) -> str | None:
    """解析唯一 running 店铺；无店/解析失败返回 None（降级不阻塞）。"""
    try:
        store_result = StoreService(session_factory).resolve_unique_running_store()
    except Exception:
        return None
    if not store_result.get("ok") or not store_result.get("store"):
        return None
    return str(store_result["store"].get("storeId") or "") or None


def run_creator_enrich(job_id: str, session_factory) -> str:
    def warning(message: str) -> None:
        append_event(
            session_factory,
            job_id,
            level="warning",
            event_type="job.warning",
            message=message,
        )

    def cancel_check() -> None:
        if is_cancellation_requested(job_id):
            raise JobCancelled("达人资料补齐已在安全检查点取消")

    def progress(current: int, total: int, message: str) -> None:
        update_progress(session_factory, job_id, current=current, total=total, message=message)

    cancel_check()
    store_id = resolve_readonly_store(session_factory)
    cancel_check()
    update_progress(session_factory, job_id, current=0, total=1, message="补齐达人资料")
    result = CreatorEnrichService(
        session_factory,
        warning=warning,
        cancel_check=cancel_check,
        store_id=store_id,
        progress=progress,
    ).enrich()
    update_progress(session_factory, job_id, current=1, total=1, message="资料补齐完成")
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
