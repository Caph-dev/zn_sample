"""CSV export job creation and local download API."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from assistant.database.models import Job
from assistant.jobs.locks import create_or_get_pending_job
from assistant.services.export_service import SUPPORTED_EXPORT_KINDS


router = APIRouter()


class ExportInput(BaseModel):
    kind: str


@router.post("/api/jobs/report-export")
def create_export(body: ExportInput, request: Request) -> dict:
    kind = "day_10_list" if body.kind == "overdue_10" else body.kind
    if kind not in SUPPORTED_EXPORT_KINDS:
        raise HTTPException(400, {"error": "unsupported-export-kind"})
    job_id, deduplicated = create_or_get_pending_job(request.app.state.session_factory, job_type="report_export", store_id=kind)
    with request.app.state.session_factory() as session:
        job = session.get(Job, job_id)
        if job and not deduplicated:
            job.result_summary = json.dumps({"kind": kind})
            session.commit()
    return {"job_id": job_id, "deduplicated": deduplicated}


@router.get("/api/exports/download")
def download(path: str) -> FileResponse:
    file_path = Path(path).resolve()
    if not file_path.is_file() or "exports" not in file_path.parts:
        raise HTTPException(404, "export-not-found")
    return FileResponse(file_path)
