"""Local-only follow-up review actions; no platform sends."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from assistant.database.models import FollowupTask, SampleCase
from assistant.jobs.locks import create_or_get_pending_job
from assistant.services.followup_service import FollowupService


router = APIRouter()


@router.post("/api/jobs/followup-generate")
def create_followup_generate(request: Request) -> dict:
    job_id, deduplicated = create_or_get_pending_job(
        request.app.state.session_factory,
        job_type="followup_generate",
        store_id=None,
    )
    return {"job_id": job_id, "deduplicated": deduplicated}


class TypeInput(BaseModel):
    creator_type: str


class LanguageInput(BaseModel):
    lang: str


async def _read_value(request: Request, model_class, field_name: str) -> tuple[str, bool]:
    is_json = "application/json" in request.headers.get("content-type", "").lower()
    if is_json:
        try:
            model = model_class.model_validate(await request.json())
        except Exception as error:
            raise HTTPException(400, f"invalid-{field_name}") from error
        return str(getattr(model, field_name)), True
    form = await request.form()
    return str(form.get(field_name) or ""), False


def _local_response(task_id: int, is_json: bool):
    if is_json:
        return {"ok": True}
    return RedirectResponse(f"/followups/{task_id}", status_code=303)


@router.post("/api/followups/{task_id}/skip")
async def skip(task_id: int, request: Request):
    with request.app.state.session_factory() as session:
        task = session.get(FollowupTask, task_id)
        if task is None:
            raise HTTPException(404, "followup-not-found")
        task.status = "skipped"
        task.send_result = "manual"
        session.commit()
    is_json = "application/json" in request.headers.get("content-type", "").lower()
    return _local_response(task_id, is_json)


@router.post("/api/followups/{task_id}/set-type")
async def set_type(task_id: int, request: Request):
    creator_type, is_json = await _read_value(request, TypeInput, "creator_type")
    if creator_type not in {"video", "live"}:
        raise HTTPException(400, "invalid-creator-type")
    _update_case(task_id, request, creator_type=creator_type)
    return _local_response(task_id, is_json)


@router.post("/api/followups/{task_id}/set-lang")
async def set_lang(task_id: int, request: Request):
    language, is_json = await _read_value(request, LanguageInput, "lang")
    if language not in {"en", "es"}:
        raise HTTPException(400, "invalid-language")
    _update_case(task_id, request, language=language)
    return _local_response(task_id, is_json)


def _update_case(task_id: int, request: Request, **values) -> None:
    with request.app.state.session_factory() as session:
        task = session.get(FollowupTask, task_id)
        if task is None:
            raise HTTPException(404, "followup-not-found")
        sample_case = session.get(SampleCase, task.sample_case_id)
        for key, value in values.items():
            setattr(sample_case, key, value)
        session.commit()
    FollowupService(request.app.state.session_factory).refresh_task(task_id)
