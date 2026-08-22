"""Read-only shipment API."""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from assistant.database.models import SampleCase, Shipment
from assistant.jobs.locks import create_or_get_pending_job


router = APIRouter()


@router.post("/api/jobs/shipment-sync")
def create_shipment_sync(request: Request) -> dict:
    job_id, deduplicated = create_or_get_pending_job(
        request.app.state.session_factory,
        job_type="shipment_sync",
        store_id=None,
    )
    return {"job_id": job_id, "deduplicated": deduplicated}


@router.get("/api/shipments")
def shipments(request: Request) -> list[dict]:
    with request.app.state.session_factory() as session:
        rows = session.execute(select(Shipment, SampleCase).join(SampleCase, Shipment.sample_case_id == SampleCase.id)).all()
        return [{"id": shipment.id, "creator_name": case.creator_name, "main_order_id": case.main_order_id, "tracking": shipment.tracking_display, "status_category": shipment.status_category} for shipment, case in rows]
