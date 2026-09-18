"""自动审批 API：规则选项、预览、执行与核对。

CSRF 由 app 层 Origin 校验统一兜底；这里专注业务校验与任务创建。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from assistant.database.models import Job
from assistant.services import auto_approval_service
from assistant.services.auto_approval_service import AutoApprovalServiceError

router = APIRouter()


def _session_factory(request: Request):
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise HTTPException(status_code=503, detail="job-database-unavailable")
    return session_factory


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as error:
        raise HTTPException(status_code=400, detail="invalid-json-body") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body-must-be-object")
    return payload


def _service_error_to_http(error: AutoApprovalServiceError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.summary},
    )


def _job_payload(job: Job | None) -> dict[str, Any]:
    if job is None:
        return {}
    return {
        "job_id": job.id,
        "status": job.status,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "progress_message": job.progress_message,
        "error_code": job.error_code,
        "error_summary": job.error_summary,
        "log_path": job.log_path,
    }


def _store_for_request(request: Request, store_id: str | None) -> str:
    """预览/执行必须有明确店铺；未传时退回唯一 running 店。"""
    explicit = str(store_id or "").strip()
    if explicit:
        return explicit
    from assistant.api.stores import running_store_summary

    summary = running_store_summary()
    if summary.get("ok") and summary.get("store"):
        return str(summary["store"].get("storeId") or "")
    raise HTTPException(
        status_code=409,
        detail={
            "code": "store-required",
            "message": "未指定店铺，且当前没有唯一运行的店铺。",
        },
    )


@router.get("/api/auto-approval/options")
def get_options(request: Request) -> dict:
    session_factory = getattr(request.app.state, "session_factory", None)
    return auto_approval_service.options_payload(session_factory)


@router.put("/api/auto-approval/rule-draft")
async def save_rule_draft(request: Request) -> dict:
    """保存自定义规则草稿，供下次进入页面回填（不作为执行授权）。"""
    session_factory = _session_factory(request)
    body = await _json_body(request)
    try:
        return auto_approval_service.save_rule_draft(
            session_factory,
            body.get("rule") or {},
        )
    except AutoApprovalServiceError as error:
        raise _service_error_to_http(error) from error


@router.get("/api/auto-approval/hero-refresh")
def refresh_hero() -> dict:
    return auto_approval_service.load_hero_products(force=True)


@router.post("/api/auto-approval/previews")
async def create_preview(request: Request) -> dict:
    session_factory = _session_factory(request)
    body = await _json_body(request)
    rule_payload = body.get("rule")
    store_id = _store_for_request(request, body.get("store_id"))
    try:
        return auto_approval_service.create_preview(
            session_factory,
            rule_payload=rule_payload,
            store_id=store_id,
        )
    except AutoApprovalServiceError as error:
        raise _service_error_to_http(error) from error


@router.get("/api/auto-approval/previews/{preview_id}")
def get_preview(preview_id: str, request: Request) -> dict:
    session_factory = _session_factory(request)
    try:
        payload = auto_approval_service.preview_payload(
            session_factory, preview_id, include_rows=True
        )
    except AutoApprovalServiceError as error:
        raise _service_error_to_http(error) from error
    with session_factory() as session:
        job = session.get(Job, payload.get("job_id") or "")
    payload["job"] = _job_payload(job)
    return payload


@router.post("/api/auto-approval/executions")
async def create_execution(request: Request) -> dict:
    session_factory = _session_factory(request)
    body = await _json_body(request)
    try:
        return auto_approval_service.create_execution(
            session_factory,
            preview_id=str(body.get("preview_id") or ""),
            apply_ids=body.get("apply_ids") or [],
            limit=body.get("limit") or 1,
            write_feishu=bool(body.get("write_feishu")),
            confirmation=str(body.get("confirmation") or ""),
            idempotency_key=str(body.get("idempotency_key") or "") or str(uuid.uuid4()),
        )
    except AutoApprovalServiceError as error:
        raise _service_error_to_http(error) from error


@router.get("/api/auto-approval/executions")
def list_executions(
    request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
) -> dict:
    from assistant.database.models import AutoApprovalExecution

    with _session_factory(request)() as session:
        executions = session.scalars(
            select(AutoApprovalExecution)
            .order_by(AutoApprovalExecution.created_at.desc(), AutoApprovalExecution.id.desc())
            .offset(offset).limit(limit + 1)
        ).all()
        return {
            "has_more": len(executions) > limit,
            "executions": [
                {
                    "execution_id": execution.id, "preview_id": execution.preview_id,
                    "store_id": execution.store_id, "status": execution.status,
                    "write_feishu": execution.write_feishu,
                    "created_at": execution.created_at.isoformat(),
                    "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
                }
                for execution in executions[:limit]
            ],
        }


@router.get("/api/auto-approval/executions/{execution_id}")
def get_execution(execution_id: str, request: Request) -> dict:
    session_factory = _session_factory(request)
    try:
        payload = auto_approval_service.execution_payload(session_factory, execution_id)
    except AutoApprovalServiceError as error:
        raise _service_error_to_http(error) from error
    with session_factory() as session:
        job = session.get(Job, payload.get("job_id") or "")
    payload["job"] = _job_payload(job)
    return payload


@router.post("/api/auto-approval/executions/{execution_id}/reconcile")
async def reconcile_execution(execution_id: str, request: Request) -> dict:
    session_factory = _session_factory(request)
    body = await _json_body(request)
    try:
        return auto_approval_service.create_reconcile(
            session_factory,
            execution_id=execution_id,
            write_feishu=bool(body.get("write_feishu")),
            confirmation=str(body.get("confirmation") or ""),
        )
    except AutoApprovalServiceError as error:
        raise _service_error_to_http(error) from error


@router.get("/api/auto-approval/recent")
def list_recent(request: Request) -> dict:
    """最近预览与执行批次（页面刷新后恢复上下文用）。"""
    session_factory = _session_factory(request)
    from assistant.database.models import AutoApprovalExecution, AutoApprovalPreview

    with session_factory() as session:
        preview_rows = session.scalars(
            select(AutoApprovalPreview)
            .order_by(AutoApprovalPreview.created_at.desc())
            .limit(20)
        ).all()
        execution_rows = session.scalars(
            select(AutoApprovalExecution)
            .order_by(AutoApprovalExecution.created_at.desc())
            .limit(20)
        ).all()
    return {
        "previews": [
            {
                "preview_id": preview.id,
                "store_id": preview.store_id,
                "status": preview.status,
                "rule_hash": preview.rule_hash,
                "integrity_complete": preview.integrity_complete,
                "created_at": preview.created_at.isoformat(),
                "finished_at": (
                    preview.finished_at.isoformat() if preview.finished_at else None
                ),
            }
            for preview in preview_rows
        ],
        "executions": [
            {
                "execution_id": execution.id,
                "preview_id": execution.preview_id,
                "status": execution.status,
                "write_feishu": execution.write_feishu,
                "created_at": execution.created_at.isoformat(),
                "finished_at": (
                    execution.finished_at.isoformat() if execution.finished_at else None
                ),
            }
            for execution in execution_rows
        ],
    }
