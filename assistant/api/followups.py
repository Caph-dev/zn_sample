"""Local-only follow-up review actions; no platform sends."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
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


@router.post("/api/followups/{task_id}/skip")
def skip(task_id: int, request: Request) -> dict:
    with request.app.state.session_factory() as session:
        task = session.get(FollowupTask, task_id)
        if task is None:
            raise HTTPException(404, "followup-not-found")
        task.status = "skipped"
        task.send_result = "manual"
        session.commit()
    return {"ok": True}


@router.post("/api/followups/{task_id}/set-type")
def set_type(task_id: int, body: TypeInput, request: Request) -> dict:
    if body.creator_type not in {"video", "live"}:
        raise HTTPException(400, "invalid-creator-type")
    _update_case(task_id, request, creator_type=body.creator_type)
    return {"ok": True}


@router.post("/api/followups/{task_id}/set-lang")
def set_lang(task_id: int, body: LanguageInput, request: Request) -> dict:
    if body.lang not in {"en", "es"}:
        raise HTTPException(400, "invalid-language")
    _update_case(task_id, request, language=body.lang)
    return {"ok": True}


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
