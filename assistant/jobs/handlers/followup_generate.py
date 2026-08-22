"""Local follow-up task generation job handler."""
from __future__ import annotations

import json

from assistant.jobs.progress import append_event, update_progress
from assistant.services.followup_service import FollowupService


def run_followup_generate(job_id: str, session_factory) -> str:
    def warning(message: str) -> None:
        append_event(session_factory, job_id, level="warning", event_type="job.warning", message=message)

    update_progress(session_factory, job_id, current=0, total=1, message="生成跟进待办")
    result = FollowupService(session_factory, warning=warning).generate()
    update_progress(session_factory, job_id, current=1, total=1, message="待办生成完成")
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
