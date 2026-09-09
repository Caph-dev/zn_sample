"""Server-rendered local assistant pages."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select

from assistant.api.jobs import router as jobs_api_router
from assistant.api.dashboard import dashboard_summary, router as dashboard_api_router
from assistant.api.exports import router as exports_api_router
from assistant.api.followups import router as followups_api_router
from assistant.api.shipments import router as shipments_api_router
from assistant.api.stores import running_store_summary
from assistant.api.auto_approval import router as auto_approval_api_router
from assistant.database.models import FollowupTask, Job, SampleCase, Shipment, Store
from assistant.domain.followup_labels import (
    FOLLOWUP_LANGUAGE_LABELS,
    FOLLOWUP_PLATFORM_STATUS_FILTER_LABELS,
    FOLLOWUP_STAGE_LABELS,
    FOLLOWUP_STAGE_LIST_ORDER,
    FOLLOWUP_STAGE_TONES,
    FOLLOWUP_STATUS_LABELS,
    FOLLOWUP_STATUS_TONES,
    SUPERSEDED_REASON,
    followup_action_completed,
    followup_action_display,
    followup_action_tone,
    followup_language_label,
    followup_status_display,
)
from assistant.jobs.locks import request_safe_store_summary
from assistant.paths import database_path, user_data_dir
from assistant.security.secret_redaction import redact_text


router = APIRouter()
router.include_router(jobs_api_router)
router.include_router(dashboard_api_router)
router.include_router(exports_api_router)
router.include_router(followups_api_router)
router.include_router(shipments_api_router)
router.include_router(auto_approval_api_router)
TEMPLATE_DIRECTORY = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIRECTORY)
templates.env.globals.update(
    followup_action_completed=followup_action_completed,
    followup_action_display=followup_action_display,
    followup_action_tone=followup_action_tone,
    followup_language_label=followup_language_label,
    followup_status_display=followup_status_display,
    FOLLOWUP_STAGE_LABELS=FOLLOWUP_STAGE_LABELS,
    FOLLOWUP_STAGE_TONES=FOLLOWUP_STAGE_TONES,
    FOLLOWUP_STATUS_TONES=FOLLOWUP_STATUS_TONES,
)


# 终态任务只在这个窗口内恢复到全局面板：刚跑完刷新还能看到结果，
# 但不会把几天前的旧任务重新挂到每个页面上。
TERMINAL_PANEL_RESTORE_WINDOW = timedelta(minutes=30)


def _latest_active_job(request: Request):
    """刷新后恢复面板用：优先最近的运行中任务，否则只回退到刚结束的任务。

    终态任务只取最近 30 分钟内结束的，避免打开任意页面都弹出旧任务输出。
    要看历史任务请走「任务」页。
    """
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        return None
    try:
        with session_factory() as session:
            active_job = session.scalar(
                select(Job)
                .where(Job.status.in_(("pending", "running")))
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            if active_job is not None:
                return active_job
            # SQLite 的 DateTime 列会剥掉时区（见 assistant/database/types.py），
            # 所以这里用 Python 过滤时间窗口，避免字符串比较踩坑。
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - TERMINAL_PANEL_RESTORE_WINDOW
            latest_finished = session.scalar(
                select(Job)
                .where(Job.status.in_(("succeeded", "failed", "cancelled", "interrupted")))
                .order_by(Job.finished_at.desc())
                .limit(1)
            )
            if latest_finished is not None and latest_finished.finished_at is not None:
                if latest_finished.finished_at >= cutoff:
                    return latest_finished
            return None
    except Exception:
        return None


def _base_context(request: Request) -> dict:
    from assistant.app import application_version

    return {
        "request": request,
        "app_name": "ZnSampleAssistant",
        "version": application_version(),
        "active_job": _latest_active_job(request),
    }


def _request_store_summary(request: Request) -> dict:
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        return running_store_summary()
    return request_safe_store_summary(session_factory)


def _debug_home_jobs(session_factory) -> None:
    from lib.debug_log import debug_log

    try:
        with session_factory() as session:
            active_jobs = session.scalars(
                select(Job)
                .where(Job.status.in_({"pending", "running"}))
                .order_by(Job.created_at.desc())
                .limit(5)
            ).all()
            latest_sync = session.scalar(
                select(Job)
                .where(Job.job_type == "shipment_sync")
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        debug_log(
            "home-render",
            location="assistant/web/routes.py:home",
            active_jobs=[
                {
                    "id": job.id,
                    "type": job.job_type,
                    "status": job.status,
                    "progress": f"{job.progress_current}/{job.progress_total}",
                }
                for job in active_jobs
            ],
            latest_shipment_sync=(
                {
                    "id": latest_sync.id,
                    "status": latest_sync.status,
                    "finished_at": latest_sync.finished_at.isoformat()
                    if latest_sync.finished_at
                    else None,
                }
                if latest_sync
                else None
            ),
        )
    except Exception as exc:  # instrumentation must never break pages
        debug_log(
            "home-render-error",
            location="assistant/web/routes.py:home",
            error=str(exc),
        )


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    context = _base_context(request)
    session_factory = getattr(request.app.state, "session_factory", None)
    context.update(
        {
            "data_directory": redact_text(user_data_dir()),
            "database_ready": database_path().is_file(),
            "store_summary": _request_store_summary(request),
            "dashboard": (
                dashboard_summary(session_factory) if session_factory else {}
            ),
        }
    )
    if session_factory is not None:
        _debug_home_jobs(session_factory)
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


@router.get("/jobs/{job_id}/status", response_class=HTMLResponse)
def job_status_partial(job_id: str, request: Request) -> HTMLResponse:
    """HTML partial for the htmx status poll on the job detail page."""
    context = _base_context(request)
    session_factory = getattr(request.app.state, "session_factory", None)
    job = None
    if session_factory is not None:
        with session_factory() as session:
            job = session.get(Job, job_id)
    if job is None:
        return HTMLResponse("任务不存在", status_code=404)
    context["job"] = job
    return templates.TemplateResponse(request, "_job_status.html", context)


@router.get("/shipments", response_class=HTMLResponse)
def shipments_page(request: Request, status: str = "") -> HTMLResponse:
    context = _base_context(request)
    with request.app.state.session_factory() as session:
        query = select(Shipment, SampleCase).join(
            SampleCase,
            Shipment.sample_case_id == SampleCase.id,
        )
        if status:
            query = query.where(Shipment.status_category == status)
        context["rows"] = session.execute(query).all()
    context["shipment_filter"] = status
    return templates.TemplateResponse(request, "shipments.html", context)


@router.get("/shipments/{shipment_id}", response_class=HTMLResponse)
def shipment_detail_page(shipment_id: int, request: Request) -> HTMLResponse:
    context = _base_context(request)
    with request.app.state.session_factory() as session:
        row = session.execute(
            select(Shipment, SampleCase).join(SampleCase, Shipment.sample_case_id == SampleCase.id).where(Shipment.id == shipment_id)
        ).first()
    if row is None:
        return HTMLResponse("物流记录不存在", status_code=404)
    context["shipment"], context["sample_case"] = row
    return templates.TemplateResponse(request, "shipment_detail.html", context)


def _followup_filter_options() -> dict[str, list[tuple[str, str]]]:
    return {
        "stages": [
            (stage, FOLLOWUP_STAGE_LABELS[stage]) for stage in FOLLOWUP_STAGE_LIST_ORDER
        ],
        "statuses": list(FOLLOWUP_STATUS_LABELS.items()),
        "languages": list(FOLLOWUP_LANGUAGE_LABELS.items()),
        "platform_statuses": list(FOLLOWUP_PLATFORM_STATUS_FILTER_LABELS.items()),
    }


def _followup_stage_order_expression():
    return case(
        *(
            (FollowupTask.stage == stage, rank)
            for rank, stage in enumerate(FOLLOWUP_STAGE_LIST_ORDER)
        ),
        else_=len(FOLLOWUP_STAGE_LIST_ORDER),
    )


@router.get("/followups", response_class=HTMLResponse)
def followups_page(
    request: Request,
    stage: str = "",
    status: str = "",
    language: str = "",
    platform_status: str = "",
    curr_status: int | None = None,
) -> HTMLResponse:
    context = _base_context(request)
    with request.app.state.session_factory() as session:
        query = (
            select(FollowupTask, SampleCase).join(SampleCase, FollowupTask.sample_case_id == SampleCase.id)
        )
        if stage:
            query = query.where(FollowupTask.stage == stage)
        if status:
            query = query.where(FollowupTask.status == status)
        if language:
            query = query.where(FollowupTask.language == language)
        if platform_status:
            query = query.where(SampleCase.platform_status == platform_status)
        if curr_status is not None:
            query = query.where(SampleCase.curr_status == curr_status)
        if not status:
            query = query.where(
                (FollowupTask.status != "suppressed")
                | (FollowupTask.suppressed_reason != SUPERSEDED_REASON)
            )
        query = query.order_by(
            _followup_stage_order_expression(),
            func.lower(SampleCase.creator_id),
            FollowupTask.id,
        )
        context["rows"] = session.execute(query).all()
    context["filters"] = {
        "stage": stage,
        "status": status,
        "language": language,
        "platform_status": platform_status,
        "curr_status": curr_status,
        "include_superseded": bool(status),
    }
    context["filter_options"] = _followup_filter_options()
    return templates.TemplateResponse(request, "followups.html", context)


@router.get("/followups/{task_id}", response_class=HTMLResponse)
def followup_detail_page(task_id: int, request: Request) -> HTMLResponse:
    context = _base_context(request)
    with request.app.state.session_factory() as session:
        row = session.execute(
            select(FollowupTask, SampleCase, Shipment, Store)
            .join(SampleCase, FollowupTask.sample_case_id == SampleCase.id)
            .join(Store, SampleCase.store_id == Store.id)
            .outerjoin(Shipment, Shipment.sample_case_id == SampleCase.id)
            .where(FollowupTask.id == task_id)
        ).first()
    if row is None:
        return HTMLResponse("待办不存在", status_code=404)
    context["task"], context["sample_case"], context["shipment"], context["store"] = row
    context["attachment_url"] = (
        "/static/sop-images/2-查看到货+达人跟进-b05.png"
        if Path(context["task"].attachment_key).name == "2-查看到货+达人跟进-b05.png" else ""
    )
    context["scheduled_label"] = "待确认送达日" if context["task"].stage == "confirm_delivery_time" or context["task"].scheduled_for is None else str(context["task"].scheduled_for)
    return templates.TemplateResponse(request, "followup_detail.html", context)


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "reports.html", _base_context(request))


@router.get("/auto-approval", response_class=HTMLResponse)
def auto_approval_page(request: Request) -> HTMLResponse:
    """独立 React 文档壳：不加载旧 app.css，避免样式污染 HTMX 页面。"""
    return templates.TemplateResponse(
        request,
        "auto_approval.html",
        {
            "request": request,
            "app_name": "ZnSampleAssistant",
            "version": "",
        },
    )
