"""Read-only shipment synchronization job handler."""
from __future__ import annotations

import json

from assistant.jobs.progress import append_event, update_progress
from assistant.jobs.registry import HandlerFailure
from assistant.services.shipment_service import ShipmentService
from assistant.services.store_service import StoreService


def run_shipment_sync(job_id: str, session_factory) -> str:
    store_result = StoreService(session_factory).resolve_unique_running_store()
    if not store_result.get("ok"):
        raise HandlerFailure("running-not-unique", "请只开一家店后再同步物流。")

    def warning(message: str) -> None:
        append_event(session_factory, job_id, level="warning", event_type="job.warning", message=message)

    def progress(current: int, total: int, message: str) -> None:
        update_progress(session_factory, job_id, current=current, total=total, message=message)

    result = ShipmentService(session_factory, warning=warning).synchronize_shipments(
        store_result["store"], job_progress=progress
    )
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
