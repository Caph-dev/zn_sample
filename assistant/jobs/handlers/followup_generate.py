"""Local follow-up task generation job handler."""
from __future__ import annotations

import json

from assistant.jobs.handlers.creator_enrich import resolve_readonly_store
from assistant.jobs.locks import is_cancellation_requested
from assistant.jobs.progress import append_event, update_progress
from assistant.jobs.registry import JobCancelled
from assistant.services.creator_enrich_service import CreatorEnrichService
from assistant.services.followup_service import FollowupService


def run_followup_generate(job_id: str, session_factory) -> str:
    def warning(message: str) -> None:
        append_event(session_factory, job_id, level="warning", event_type="job.warning", message=message)

    def cancel_check() -> None:
        if is_cancellation_requested(job_id):
            raise JobCancelled("跟进待办生成已在安全检查点取消")

    store_id = resolve_readonly_store(session_factory)

    # 默认前置：先补齐缺失的类型/语言，再生成待办。
    def progress(current: int, total: int, message: str) -> None:
        update_progress(session_factory, job_id, current=current, total=total, message=message)

    progress(0, 1, "补齐达人资料")
    enrich_result = CreatorEnrichService(
        session_factory,
        warning=warning,
        cancel_check=cancel_check,
        store_id=store_id,
        progress=progress,
    ).enrich()
    append_event(
        session_factory,
        job_id,
        level="info",
        event_type="job.stage",
        message=f"达人资料补齐完成：{enrich_result}",
    )

    update_progress(session_factory, job_id, current=0, total=1, message="生成跟进待办")
    result = FollowupService(
        session_factory,
        warning=warning,
        cancel_check=cancel_check,
        store_id=store_id,
    ).generate()
    update_progress(session_factory, job_id, current=1, total=1, message="待办生成完成")
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
