"""CSV report export job handler."""
from __future__ import annotations

import json

from assistant.database.models import Job
from assistant.jobs.progress import update_progress
from assistant.jobs.registry import HandlerFailure
from assistant.services.export_service import ExportService


def run_report_export(job_id: str, session_factory) -> str:
    with session_factory() as session:
        job = session.get(Job, job_id)
        try:
            request = json.loads(job.result_summary or "{}")
        except (TypeError, json.JSONDecodeError):
            request = {}
    kind = str(request.get("kind") or "")
    update_progress(session_factory, job_id, current=0, total=1, message="导出 CSV")
    try:
        output_path = ExportService(session_factory).export(kind)
    except ValueError as error:
        raise HandlerFailure("unsupported-export-kind", "不支持的导出类型。") from error
    update_progress(session_factory, job_id, current=1, total=1, message="CSV 导出完成")
    return json.dumps({"kind": kind, "path": str(output_path)}, ensure_ascii=False)
