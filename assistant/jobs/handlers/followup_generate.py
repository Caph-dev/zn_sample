"""Local follow-up task generation job handler."""
from __future__ import annotations

import json

from assistant.jobs.locks import is_cancellation_requested
from assistant.jobs.progress import append_event, update_progress
from assistant.jobs.registry import JobCancelled
from assistant.services.followup_service import FollowupService


def run_followup_generate(job_id: str, session_factory) -> str:
    def warning(message: str) -> None:
        append_event(session_factory, job_id, level="warning", event_type="job.warning", message=message)

    def cancel_check() -> None:
        if is_cancellation_requested(job_id):
            raise JobCancelled("跟进待办生成已在安全检查点取消")

    update_progress(session_factory, job_id, current=0, total=1, message="生成跟进待办")
    result = FollowupService(
        session_factory,
        warning=warning,
        cancel_check=cancel_check,
    ).generate()
    update_progress(session_factory, job_id, current=1, total=1, message="待办生成完成")
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
