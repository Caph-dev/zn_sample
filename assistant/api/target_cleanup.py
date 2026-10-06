"""Fixed form-data cleanup requests and bounded, local-only batch recovery."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from assistant.services import target_cleanup_service as service

router = APIRouter()


def _session_factory(request: Request):
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise HTTPException(503, "job-database-unavailable")
    return session_factory


def _http_error(error: service.TargetCleanupServiceError) -> HTTPException:
    return HTTPException(error.status_code, {"code": error.code, "message": error.summary})


def _batch_url(batch_id: str) -> str:
    return f"/plan-cleanup?batch_id={batch_id}"


async def _read_fixed_form(request: Request, required_fields: frozenset[str]) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        raise HTTPException(415, {"code": "form-data-required", "message": "Submit the fixed form fields."})
    if request.query_params:
        raise HTTPException(400, {"code": "invalid-form-fields", "message": "POST accepts form fields only, not query parameters."})
    try:
        async with request.form(max_files=0, max_fields=len(required_fields), max_part_size=4096) as form:
            submitted_fields = list(form.multi_items())
            if (len(submitted_fields) != len(required_fields)
                    or {name for name, _value in submitted_fields} != required_fields
                    or any(not isinstance(value, str) or not value.strip() for _name, value in submitted_fields)):
                raise ValueError("Unknown, repeated, file or missing form fields")
            return dict(submitted_fields)
    except Exception as error:
        raise HTTPException(400, {
            "code": "invalid-form-fields",
            "message": "Only the required, nonempty form fields are accepted, once each; files are not accepted.",
        }) from error


@router.post("/api/target-cleanup/previews")
async def create_preview(request: Request) -> dict:
    fields = await _read_fixed_form(request, frozenset({"months", "idempotency_key"}))
    if fields["months"] not in {"2", "4"}:
        raise HTTPException(400, {"code": "invalid-months", "message": "months must be exactly 2 or 4."})
    try:
        payload = service.create_preview(_session_factory(request), int(fields["months"]), fields["idempotency_key"])
    except service.TargetCleanupServiceError as error:
        raise _http_error(error) from error
    return {**payload, "batch_url": _batch_url(payload["batch_id"])}


@router.post("/api/target-cleanup/batches/{batch_id}/execute")
async def create_execution(batch_id: str, request: Request) -> dict:
    fields = await _read_fixed_form(request, frozenset({"confirmation", "idempotency_key"}))
    try:
        payload = service.create_execution(
            _session_factory(request), batch_id, fields["confirmation"], fields["idempotency_key"],
        )
    except service.TargetCleanupServiceError as error:
        raise _http_error(error) from error
    return {**payload, "batch_url": _batch_url(payload["batch_id"])}


@router.get("/api/target-cleanup/batches")
def list_batches(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=20),
    idempotency_key: str = Query("", max_length=128),
) -> dict:
    session_factory = _session_factory(request)
    try:
        payload = service.list_batches(session_factory, offset, limit, idempotency_key)
        # Recover only the bounded visible slice, never probe a store or requeue work.
        for batch in payload["batches"]:
            service.recover_batch(session_factory, batch["batch_id"])
        payload = service.list_batches(session_factory, offset, limit, idempotency_key)
    except service.TargetCleanupServiceError as error:
        raise _http_error(error) from error
    payload["batches"] = [{**batch, "batch_url": _batch_url(batch["batch_id"])} for batch in payload["batches"]]
    return payload


@router.get("/api/target-cleanup/batches/{batch_id}")
def get_batch(
    batch_id: str,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> dict:
    session_factory = _session_factory(request)
    try:
        service.recover_batch(session_factory, batch_id)
        payload = service.batch_payload(session_factory, batch_id, offset, limit)
    except service.TargetCleanupServiceError as error:
        raise _http_error(error) from error
    return {**payload, "batch_url": _batch_url(payload["batch_id"])}
