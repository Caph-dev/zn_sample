"""Server-rendered local assistant pages."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from assistant.api.jobs import router as jobs_api_router
from assistant.api.stores import running_store_summary
from assistant.database.models import Job
from assistant.jobs.locks import request_safe_store_summary
from assistant.paths import database_path, user_data_dir
from assistant.security.secret_redaction import redact_text


router = APIRouter()
router.include_router(jobs_api_router)
TEMPLATE_DIRECTORY = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIRECTORY)


def _base_context(request: Request) -> dict:
    from assistant.app import application_version

    return {
        "request": request,
        "app_name": "ZnSampleAssistant",
        "version": application_version(),
        "csrf_token": request.state.session["csrf"],
    }


def _request_store_summary(request: Request) -> dict:
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        return running_store_summary()
    return request_safe_store_summary(session_factory)


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    context = _base_context(request)
    context.update(
        {
            "data_directory": redact_text(user_data_dir()),
            "database_ready": database_path().is_file(),
            "store_summary": _request_store_summary(request),
        }
    )
    return templates.TemplateResponse(request, "home.html", context)


@router.get("/diagnostics", response_class=HTMLResponse)
def diagnostics(request: Request) -> HTMLResponse:
    from lib.app_config import load_raw_config, resolve_config_path
    from lib.zclaw_cli import resolve_ziniao_cli_command

    context = _base_context(request)
    cli_state = "READY"
    try:
        resolve_ziniao_cli_command()
    except RuntimeError:
        cli_state = "CLI_NOT_FOUND"
    config_path = resolve_config_path()
    raw_config = load_raw_config() if config_path else {}
    feishu = raw_config.get("feishu") if isinstance(raw_config, dict) else {}
    store_summary = _request_store_summary(request)
    bridge_state = (
        "BRIDGE_UNAVAILABLE"
        if store_summary.get("error") == "running-query-failed"
        else "ZINIAO_BUSY"
        if store_summary.get("error") == "ziniao-busy"
        else "READY"
    )
    context.update(
        {
            "python_version": sys.version.split()[0],
            "cli_state": cli_state,
            "bridge_state": bridge_state,
            "store_summary": store_summary,
            "config_exists": bool(config_path),
            "feishu_configured": bool(isinstance(feishu, dict) and feishu),
        }
    )
    return templates.TemplateResponse(request, "diagnostics.html", context)


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    context = _base_context(request)
    session_factory = getattr(request.app.state, "session_factory", None)
    jobs = []
    if session_factory is not None:
        with session_factory() as session:
            jobs = session.scalars(
                select(Job).order_by(Job.created_at.desc()).limit(100)
            ).all()
    context["jobs"] = jobs
    return templates.TemplateResponse(request, "jobs.html", context)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail_page(job_id: str, request: Request) -> HTMLResponse:
    context = _base_context(request)
    session_factory = getattr(request.app.state, "session_factory", None)
    job = None
    if session_factory is not None:
        with session_factory() as session:
            job = session.get(Job, job_id)
    if job is None:
        return HTMLResponse("任务不存在", status_code=404)
    context["job"] = job
    return templates.TemplateResponse(request, "job_detail.html", context)
