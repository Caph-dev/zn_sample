"""CSV exports for local read-only follow-up views."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from assistant.database.models import FollowupTask, SampleCase, Shipment, Store
from assistant.domain.followup_stage import followup_task_completed
from assistant.domain.timeutil import beijing_now
from assistant.paths import user_data_dir


EXPORT_FIELDS = (
    "store_name", "creator_name", "creator_id", "product_id", "apply_id",
    "curr_status", "platform_status", "main_order_id", "tracking_raw", "delivered_at", "stage",
    "action_kind", "language", "creator_type", "template_key", "reason",
)
SUPPORTED_EXPORT_KINDS = frozenset({"today", "day_10_list", "logistics_exception", "needs_review"})
SPREADSHEET_FORMULA_PREFIXES = ("=", "+", "-", "@")


def serialize_csv_cell_value(value: object) -> object:
    """Mark formula-like string cells as text during CSV serialization."""
    if isinstance(value, str) and value.startswith(SPREADSHEET_FORMULA_PREFIXES):
        return f"'{value}"
    return value


class ExportService:
    def __init__(self, session_factory, *, exports_directory: Path | None = None) -> None:
        self.session_factory = session_factory
        self.exports_directory = exports_directory or user_data_dir() / "exports"

    def export(self, kind: str) -> Path:
        normalized_kind = "day_10_list" if kind == "overdue_10" else kind
        if normalized_kind not in SUPPORTED_EXPORT_KINDS:
            raise ValueError("unsupported-export-kind")
        rows = self._rows(normalized_kind)
        self.exports_directory.mkdir(parents=True, exist_ok=True)
        if normalized_kind == "day_10_list":
            date_stamp = beijing_now().strftime("%Y%m%d")
            output_name = f"处理中超过10天_{date_stamp}.csv"
        else:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output_name = f"{normalized_kind}_{timestamp}.csv"
        output_path = self.exports_directory / output_name
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS)
            writer.writeheader()
            writer.writerows(
                {
                    field_name: serialize_csv_cell_value(row[field_name])
                    for field_name in EXPORT_FIELDS
                }
                for row in rows
            )
        return output_path

    def _rows(self, kind: str) -> list[dict]:
        with self.session_factory() as session:
            results = session.execute(
                select(FollowupTask, SampleCase, Shipment, Store)
                .join(SampleCase, FollowupTask.sample_case_id == SampleCase.id)
                .join(Store, SampleCase.store_id == Store.id)
                .outerjoin(Shipment, Shipment.sample_case_id == SampleCase.id)
            ).all()
            rows = []
            for task, sample_case, shipment, store in results:
                if kind == "day_10_list" and not (
                    task.stage == "day_10_list"
                    and sample_case.platform_status == "processing"
                    and not sample_case.platform_status_stale
                    and not followup_task_completed(
                        {"sent_at": task.sent_at, "send_result": task.send_result}
                    )
                ):
                    continue
                if kind == "logistics_exception" and not (
                    shipment and (
                        shipment.status_category in {"exception", "returned", "lost"}
                        or shipment.needs_delivery_time_confirmation
                    )
                ):
                    continue
                if kind == "needs_review" and not task.requires_manual_confirmation:
                    continue
                if kind == "today" and (
                    task.status not in {"pending", "needs_review"}
                    or followup_task_completed(
                        {"sent_at": task.sent_at, "send_result": task.send_result}
                    )
                ):
                    continue
                rows.append({
                    "store_name": store.store_name,
                    "creator_name": sample_case.creator_name,
                    "creator_id": sample_case.creator_id,
                    "product_id": sample_case.product_id,
                    "apply_id": sample_case.apply_id,
                    "curr_status": sample_case.curr_status,
                    "platform_status": sample_case.platform_status,
                    "main_order_id": sample_case.main_order_id,
                    "tracking_raw": shipment.tracking_display if shipment else "",
                    "delivered_at": shipment.delivered_at.isoformat() if shipment and shipment.delivered_at else "",
                    "stage": task.stage,
                    "action_kind": task.action_kind,
                    "language": task.language,
                    "creator_type": task.creator_type,
                    "template_key": task.template_key,
                    "reason": task.suppressed_reason or task.last_error,
                })
            return rows
