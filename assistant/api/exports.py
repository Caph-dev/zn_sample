"""CSV export job creation and local download API."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from assistant.jobs.locks import create_or_get_pending_job
from assistant.services.export_service import SUPPORTED_EXPORT_KINDS


router = APIRouter()


class ExportInput(BaseModel):
    kind: str


async def _read_export_kind(request: Request) -> str:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            payload = ExportInput.model_validate(await request.json())
        except Exception as error:
            raise HTTPException(400, {"error": "unsupported-export-kind"}) from error
        return payload.kind
    form = await request.form()
    return str(form.get("kind") or "")


@router.post("/api/jobs/report-export")
async def create_export(request: Request) -> dict:
    requested_kind = await _read_export_kind(request)
    kind = "day_10_list" if requested_kind == "overdue_10" else requested_kind
    if kind not in SUPPORTED_EXPORT_KINDS:
        raise HTTPException(400, {"error": "unsupported-export-kind"})
    job_id, deduplicated = create_or_get_pending_job(
        request.app.state.session_factory,
        job_type="report_export",
        store_id=kind,
        result_summary=json.dumps({"kind": kind}),
    )
    return {"job_id": job_id, "deduplicated": deduplicated}


@router.get("/api/exports/download")
def download(path: str = "", filename: str = "") -> FileResponse:
    from assistant import paths as assistant_paths

    exports_directory = (assistant_paths.user_data_dir() / "exports").resolve()
    requested_value = filename or path
    candidate = Path(requested_value)
    file_path = (
        (exports_directory / candidate).resolve()
        if filename or not candidate.is_absolute()
        else candidate.resolve()
    )
    try:
        file_path.relative_to(exports_directory)
    except ValueError as error:
        raise HTTPException(404, "export-not-found") from error
    if file_path.suffix.lower() != ".csv" or not file_path.is_file():
        raise HTTPException(404, "export-not-found")
    return FileResponse(file_path)
