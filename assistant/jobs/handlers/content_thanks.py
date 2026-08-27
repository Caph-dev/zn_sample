"""Dry-run SOP 2 completed-content thanks preview."""
from __future__ import annotations

import json

from assistant.jobs.locks import is_cancellation_requested
from assistant.jobs.progress import append_event, update_progress
from assistant.jobs.registry import JobCancelled
from assistant.services.content_thanks_service import ContentThanksService


def run_content_thanks_preview(job_id: str, session_factory) -> str:
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
            raise JobCancelled("已完成感谢预演已在安全检查点取消")

    def progress(current: int, total: int, message: str) -> None:
        update_progress(
            session_factory,
            job_id,
            current=current,
            total=total,
            message=message,
        )

    update_progress(session_factory, job_id, current=0, total=1, message="预演已完成感谢私信")
    result = ContentThanksService(
        session_factory,
        warning=warning,
        cancel_check=cancel_check,
        progress=progress,
        execute=False,
        write_feishu=False,
    ).preview()
    update_progress(session_factory, job_id, current=1, total=1, message="已完成感谢预演结束")
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
