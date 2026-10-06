"""Transactional, server-owned target-plan previews and one-shot execution.

The database owns identity, request keys, snapshot digests and execution claims.
CSV is presentation only. Result journals may restore outcomes, never original
candidate evidence or a permission to retry a possibly submitted operation.
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlencode

from sqlalchemy import func, or_, select, text, update

from assistant.database.models import Job, TargetCleanupBatch, TargetCleanupItem
from assistant.jobs import locks
from assistant.paths import ensure_user_dirs
from assistant.services.page_lock import blocking_job_types
from assistant.services.release_lifecycle import admission_guard
from lib import target_plan_cleanup as core

PREVIEW_JOB_TYPE = "target_cleanup_preview"
EXECUTE_JOB_TYPE = "target_cleanup_execute"
ACTIVE_JOB_STATUSES = ("pending", "running")
BLOCKED_ITEM_STATUSES = ("submitted", "uncertain", "attempting")
TERMINAL_ITEM_STATUSES = frozenset({"submitted", "skipped", "failed", "uncertain", "not_processed"})
ENVELOPE_FIELDS = {"schema_version", "batch_id", "job_id", "snapshot_sha256", "confirmed_at", "confirmation"}
logger = logging.getLogger(__name__)


class TargetCleanupServiceError(RuntimeError):
    def __init__(self, code: str, summary: str = "", status_code: int = 409) -> None:
        self.code = code
        self.summary = summary or code
        self.status_code = status_code
        super().__init__(self.summary)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_text(payload: Any) -> str:
    return core.canonical_json(payload).decode("utf-8")


def _decode_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError) as error:
        raise TargetCleanupServiceError("invalid-persisted-evidence") from error


def _batch_identifier(batch_id: str) -> str:
    try:
        if not isinstance(batch_id, str) or str(uuid.UUID(batch_id)) != batch_id:
            raise ValueError("canonical UUID required")
    except (ValueError, TypeError, AttributeError) as error:
        raise TargetCleanupServiceError("invalid-batch-id", status_code=400) from error
    return batch_id


def cleanup_directory() -> Path:
    root = ensure_user_dirs()
    directory = root / "exports" / "target_cleanup"
    if any(path.is_symlink() for path in (root, root / "exports", directory)):
        raise TargetCleanupServiceError("unsafe-artifact-path")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def artifact_paths(batch_id: str) -> dict[str, Path]:
    """Absolute worker paths; persisted pointers remain user-root relative."""
    return core.artifact_paths(cleanup_directory(), _batch_identifier(batch_id))


def _relative_paths(paths: dict[str, Path]) -> dict[str, str]:
    root = cleanup_directory().parent.parent
    return {name: path.relative_to(root).as_posix() for name, path in paths.items()}


def _verified_paths(batch: TargetCleanupBatch) -> dict[str, Path]:
    paths = artifact_paths(batch.id)
    expected = _relative_paths(paths)
    if _decode_json(batch.artifacts_json) != expected or batch.snapshot_path != expected["snapshot"]:
        raise TargetCleanupServiceError("artifact-binding-mismatch")
    if any(path.is_symlink() for path in paths.values()):
        raise TargetCleanupServiceError("unsafe-artifact-path")
    return paths


@contextmanager
def _transaction(session_factory):
    # Serialize with existing admission/creation and SQLite's cross-process lock.
    with admission_guard(session_factory), locks._job_creation_lock:
        with session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise


def _get_batch(session, batch_id: str) -> TargetCleanupBatch:
    batch = session.get(TargetCleanupBatch, _batch_identifier(batch_id))
    if batch is None:
        raise TargetCleanupServiceError("batch-not-found", status_code=404)
    return batch


def _require_idle(session, *, ignore_job_id: str = "") -> None:
    occupied = session.scalar(
        select(Job.id).where(
            Job.status.in_(ACTIVE_JOB_STATUSES),
            Job.job_type.in_(set(blocking_job_types()) | {PREVIEW_JOB_TYPE, EXECUTE_JOB_TYPE}),
            Job.id != ignore_job_id,
        ).limit(1)
    )
    if occupied is not None:
        raise TargetCleanupServiceError("store-busy", "A pending or running task occupies the store page.")


def resolve_running_identity() -> dict[str, str]:
    """One read-only identity inspection; no navigation or store fallback."""
    from lib import zclaw
    from lib.target_invitation_navigation import (
        INSPECT_NAVIGATION_PAGE_JS, shop_context_from_page_state, validate_navigation_start,
    )

    try:
        stores = zclaw.list_running_stores()
        if not isinstance(stores, list) or len(stores) != 1 or not isinstance(stores[0], dict):
            raise TargetCleanupServiceError("running-not-unique")
        store_id = str(stores[0].get("storeId") or "").strip()
        if not store_id:
            raise TargetCleanupServiceError("invalid-store-identity")
        page = zclaw.zclaw_exec(store_id, INSPECT_NAVIGATION_PAGE_JS, timeout=15, retries=0)
        if not isinstance(page, dict) or page.get("ok") is not True:
            raise TargetCleanupServiceError("unknown-page-identity")
        validate_navigation_start(page)
        shop_id, shop_region = shop_context_from_page_state(page)
        query = parse_qs(urlsplit(str(page.get("href") or "")).query)
        if "shop_id" in query and (len(query["shop_id"]) != 1 or query["shop_id"][0].strip() != shop_id):
            raise TargetCleanupServiceError("shop-context-mismatch")
        if not shop_id:
            raise TargetCleanupServiceError("unknown-shop-identity")
        return {
            "store_id": store_id, "store_name": str(stores[0].get("storeName") or ""),
            "shop_id": shop_id, "shop_region": shop_region,
        }
    except TargetCleanupServiceError:
        raise
    except Exception as error:
        raise TargetCleanupServiceError("identity-unavailable", "Read-only store identity verification failed.", 503) from error


def _require_identity(batch: TargetCleanupBatch) -> None:
    identity = resolve_running_identity()
    if identity["store_id"] != batch.store_id or identity["shop_id"] != batch.shop_id:
        raise TargetCleanupServiceError("store-identity-mismatch")
    if identity["shop_region"] != batch.shop_region:
        raise TargetCleanupServiceError("shop-context-mismatch")


def _request_key(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 128:
        raise TargetCleanupServiceError("invalid-idempotency-key", status_code=400)
    return idempotency_key.strip()


def _deduplicated_request(session, key: str, fingerprint: str, phase: str) -> dict[str, Any] | None:
    batch = session.scalar(select(TargetCleanupBatch).where(or_(
        TargetCleanupBatch.preview_idempotency_key == key,
        TargetCleanupBatch.execute_idempotency_key == key,
    )))
    if batch is None:
        return None
    existing_key = getattr(batch, f"{phase}_idempotency_key")
    existing_fingerprint = getattr(batch, f"{phase}_request_fingerprint")
    if existing_key != key or existing_fingerprint != fingerprint:
        raise TargetCleanupServiceError("idempotency-conflict")
    return {"batch_id": batch.id, "job_id": getattr(batch, f"{phase}_job_id"), "deduplicated": True}


def create_preview(session_factory, months: int, idempotency_key: str) -> dict[str, Any]:
    try:
        core.validate_months(months)
    except core.TargetCleanupError as error:
        raise TargetCleanupServiceError(error.code, error.summary, 400) from error
    key = _request_key(idempotency_key)
    fingerprint = core.payload_digest({"phase": "preview", "months": months})
    with _transaction(session_factory) as session:
        existing = _deduplicated_request(session, key, fingerprint, "preview")
        if existing is not None:
            return existing
        _require_idle(session)
        identity = resolve_running_identity()
        batch_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
        paths = _relative_paths(artifact_paths(batch_id))
        session.add(Job(
            id=job_id, job_type=PREVIEW_JOB_TYPE, store_id=identity["store_id"],
            status="pending", requested_by="local-session",
            result_summary=_json_text({"batch_id": batch_id, "mode": "preview"}),
        ))
        session.flush()
        session.add(TargetCleanupBatch(
            id=batch_id, months=months, **identity, preview_job_id=job_id,
            preview_idempotency_key=key, preview_request_fingerprint=fingerprint,
            snapshot_path=paths["snapshot"], artifacts_json=_json_text(paths),
        ))
        return {"batch_id": batch_id, "job_id": job_id, "deduplicated": False}


def _items(session, batch_id: str) -> list[TargetCleanupItem]:
    return list(session.scalars(select(TargetCleanupItem).where(
        TargetCleanupItem.batch_id == batch_id,
    ).order_by(TargetCleanupItem.position)))


def _read_bound_snapshot(session, batch: TargetCleanupBatch) -> dict[str, Any]:
    if batch.preview_status != "completed" or not batch.snapshot_sha256:
        raise TargetCleanupServiceError("preview-not-completed")
    paths = _verified_paths(batch)
    try:
        snapshot = core.read_snapshot(paths["snapshot"], batch.snapshot_sha256)
        if core.payload_digest(snapshot) != batch.snapshot_sha256:
            raise core.TargetCleanupError("noncanonical-snapshot")
        frozen = _decode_json(batch.frozen_json)
        if {field: snapshot[field] for field in core.FROZEN_FIELDS} != frozen:
            raise core.TargetCleanupError("frozen-binding-mismatch")
        if (snapshot["batch_id"], snapshot["months"], snapshot["store_id"], snapshot["shop_id"]) != (
            batch.id, batch.months, batch.store_id, batch.shop_id,
        ):
            raise core.TargetCleanupError("snapshot-binding-mismatch")
        candidates = [row for row in snapshot["rows"] if row["eligible"]]
        stored_items = _items(session, batch.id)
        if [row["invitation_id"] for row in candidates] != [item.invitation_id for item in stored_items]:
            raise core.TargetCleanupError("candidate-binding-mismatch")
        if any(_decode_json(item.row_json) != row or item.store_id != batch.store_id
               for item, row in zip(stored_items, candidates)):
            raise core.TargetCleanupError("candidate-evidence-mismatch")
        if any(item.status != "pending" for item in stored_items):
            raise core.TargetCleanupError("candidate-already-consumed")
        if (batch.scan_complete != snapshot["scan_complete"] or batch.candidate_count != len(candidates)
                or batch.preview_finished_at != core.parse_timestamp(snapshot["finished_at"])
                or batch.expires_at != core.parse_timestamp(snapshot["expires_at"])):
            raise core.TargetCleanupError("snapshot-binding-mismatch")
        core.validate_freshness(snapshot)
    except core.TargetCleanupError as error:
        raise TargetCleanupServiceError(error.code, error.summary) from error
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise TargetCleanupServiceError("snapshot-unavailable") from error
    return snapshot


def _recover_history(session, store_id: str) -> None:
    batches = session.scalars(select(TargetCleanupBatch).where(
        TargetCleanupBatch.store_id == store_id,
        TargetCleanupBatch.execute_status.in_(("queued", "running")),
    )).all()
    for previous in batches:
        _recover_terminal_batch(session, previous)


def _require_clean_history(session, batch: TargetCleanupBatch, snapshot: dict[str, Any]) -> None:
    blocked = session.scalar(select(TargetCleanupItem.invitation_id).where(
        TargetCleanupItem.store_id == batch.store_id,
        TargetCleanupItem.batch_id != batch.id,
        TargetCleanupItem.invitation_id.in_(snapshot["candidate_ids"]),
        TargetCleanupItem.status.in_(BLOCKED_ITEM_STATUSES),
    ).limit(1))
    if blocked is not None:
        raise TargetCleanupServiceError("historical-write-requires-review")


def _recover_and_require_clean_history(session, batch: TargetCleanupBatch, snapshot: dict[str, Any]) -> None:
    _recover_history(session, batch.store_id)
    try:
        _require_clean_history(session, batch, snapshot)
    except TargetCleanupServiceError:
        # Recovery is monotonic evidence, not part of the rejected new request.
        # No job/key/claim for this request has been written at this point.
        session.commit()
        raise


def _write_execution_envelope(session, batch: TargetCleanupBatch, envelope: dict[str, Any]) -> None:
    paths = _verified_paths(batch)
    try:
        core.atomic_write_json(paths["execute_envelope"], envelope, immutable=True)
        return
    except FileExistsError:
        pass
    # A crash between file publication and DB commit may leave an unused receipt.
    # Any job, attempt, backup or result evidence makes replacement unsafe.
    orphan_is_unclaimed = (
        batch.execute_status == "unclaimed" and batch.execute_job_id is None
        and batch.execute_idempotency_key is None and batch.confirmed_at is None
        and batch.execute_started_at is None and not batch.execute_envelope_json
    )
    has_execution_artifacts = any(paths[name].exists() for name in (
        "results_json", "results_csv", "backup_json", "backup_csv",
    ))
    has_item_evidence = any(item.status != "pending" or item.attempted_at is not None
                            or item.returned_at is not None or item.action
                            for item in _items(session, batch.id))
    if not orphan_is_unclaimed or has_execution_artifacts or has_item_evidence:
        raise TargetCleanupServiceError("orphan-execution-requires-review")
    try:
        path = paths["execute_envelope"]
        if path.stat().st_size > 16384:
            raise ValueError("oversized orphan")
        orphan_bytes = path.read_bytes()
        orphan = json.loads(orphan_bytes)
        if (not isinstance(orphan, dict) or set(orphan) != ENVELOPE_FIELDS
                or type(orphan["schema_version"]) is not int or orphan["schema_version"] != core.SCHEMA_VERSION
                or orphan["batch_id"] != batch.id or orphan["snapshot_sha256"] != batch.snapshot_sha256
                or orphan["confirmation"] != "y" or core.canonical_json(orphan) != orphan_bytes):
            raise ValueError("unbound orphan")
        orphan_job_id = _batch_identifier(orphan["job_id"])
        if session.get(Job, orphan_job_id) is not None:
            raise ValueError("orphan job exists")
        confirmation_time = core.parse_timestamp(orphan["confirmed_at"])
        if not batch.preview_finished_at <= confirmation_time <= _utc_now():
            raise ValueError("orphan confirmation time")
    except (OSError, ValueError, TypeError, KeyError, core.TargetCleanupError, TargetCleanupServiceError) as error:
        raise TargetCleanupServiceError("orphan-execution-requires-review") from error
    core.atomic_write_json(paths["execute_envelope"], envelope)


def create_execution(session_factory, batch_id: str, confirmation: str, idempotency_key: str) -> dict[str, Any]:
    if not isinstance(confirmation, str) or confirmation.strip().lower() != "y":
        raise TargetCleanupServiceError("confirmation-required", "Enter y to confirm.", 400)
    batch_id = _batch_identifier(batch_id)
    key = _request_key(idempotency_key)
    fingerprint = core.payload_digest({"phase": "execute", "batch_id": batch_id, "confirmation": "y"})
    with _transaction(session_factory) as session:
        existing = _deduplicated_request(session, key, fingerprint, "execute")
        if existing is not None:
            return existing
        batch = _get_batch(session, batch_id)
        if batch.execute_status != "unclaimed" or batch.execute_job_id is not None:
            raise TargetCleanupServiceError("batch-already-consumed")
        _require_idle(session)
        snapshot = _read_bound_snapshot(session, batch)
        _recover_and_require_clean_history(session, batch, snapshot)
        _require_identity(batch)
        job_id = str(uuid.uuid4())
        confirmed_at = _utc_now()
        envelope = {
            "schema_version": core.SCHEMA_VERSION, "batch_id": batch.id, "job_id": job_id,
            "snapshot_sha256": batch.snapshot_sha256, "confirmed_at": confirmed_at.isoformat(), "confirmation": "y",
        }
        try:
            _write_execution_envelope(session, batch, envelope)
        except (OSError, core.TargetCleanupError) as error:
            raise TargetCleanupServiceError("envelope-write-failed") from error
        session.add(Job(
            id=job_id, job_type=EXECUTE_JOB_TYPE, store_id=batch.store_id, status="pending",
            requested_by="local-session", progress_total=batch.candidate_count,
            result_summary=_json_text({"batch_id": batch.id, "mode": "execute"}),
        ))
        session.flush()
        batch.execute_job_id = job_id
        batch.execute_status = "queued"
        batch.execute_idempotency_key = key
        batch.execute_request_fingerprint = fingerprint
        batch.confirmed_at = confirmed_at
        batch.execute_envelope_json = _json_text(envelope)
        return {"batch_id": batch.id, "job_id": job_id, "deduplicated": False}


def _require_running_job(session, batch: TargetCleanupBatch, job_id: str, mode: str) -> Job:
    expected_id = getattr(batch, f"{mode}_job_id")
    job = session.get(Job, job_id)
    if (job_id != expected_id or job is None or job.status != "running"
            or job.job_type != f"target_cleanup_{mode}" or job.store_id != batch.store_id):
        raise TargetCleanupServiceError("job-claim-required")
    return job


def _read_envelope(batch: TargetCleanupBatch, path: Path) -> dict[str, Any]:
    envelope = _decode_json(batch.execute_envelope_json)
    if (not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS
            or type(envelope["schema_version"]) is not int or envelope["schema_version"] != core.SCHEMA_VERSION
            or envelope["batch_id"] != batch.id or envelope["job_id"] != batch.execute_job_id
            or envelope["snapshot_sha256"] != batch.snapshot_sha256 or envelope["confirmation"] != "y"
            or envelope["confirmed_at"] != batch.confirmed_at.isoformat()):
        raise TargetCleanupServiceError("invalid-execution-envelope")
    try:
        if path.stat().st_size > 16384 or path.read_bytes() != core.canonical_json(envelope):
            raise TargetCleanupServiceError("execution-envelope-mismatch")
    except OSError as error:
        raise TargetCleanupServiceError("execution-envelope-unavailable") from error
    return envelope


def begin_script(session_factory, batch_id: str, job_id: str, mode: str) -> dict[str, Any]:
    if mode not in {"preview", "execute"}:
        raise TargetCleanupServiceError("invalid-mode", status_code=400)
    with _transaction(session_factory) as session:
        batch = _get_batch(session, batch_id)
        _require_running_job(session, batch, job_id, mode)
        phase_status = getattr(batch, f"{mode}_status")
        if phase_status != "queued":
            raise TargetCleanupServiceError("batch-already-claimed")
        _require_idle(session, ignore_job_id=job_id)
        paths = _verified_paths(batch)
        snapshot, envelope = None, None
        if mode == "execute":
            snapshot = _read_bound_snapshot(session, batch)
            envelope = _read_envelope(batch, paths["execute_envelope"])
            _recover_and_require_clean_history(session, batch, snapshot)
        _require_identity(batch)
        started_at = _utc_now()
        if mode == "preview":
            frozen = core.freeze_rule(batch_id=batch.id, store_id=batch.store_id, shop_id=batch.shop_id, months=batch.months)
            batch.frozen_json = _json_text(frozen)
            batch.preview_started_at = core.parse_timestamp(frozen["started_at"])
        else:
            frozen = _decode_json(batch.frozen_json)
            batch.execute_started_at = started_at
        session.flush()
        claimed = session.execute(update(TargetCleanupBatch).where(
            TargetCleanupBatch.id == batch.id,
            getattr(TargetCleanupBatch, f"{mode}_status") == "queued",
        ).values(**{f"{mode}_status": "running"}))
        if claimed.rowcount != 1:
            raise TargetCleanupServiceError("batch-already-claimed")
        return {
            "batch_id": batch.id, "job_id": job_id, "mode": mode, "months": batch.months,
            "store_id": batch.store_id, "shop_id": batch.shop_id, "frozen": frozen,
            "paths": {name: str(path) for name, path in paths.items()},
            "snapshot_sha256": batch.snapshot_sha256, "snapshot": snapshot, "envelope": envelope,
        }


def validate_script_identity(session_factory, batch_id: str, job_id: str, mode: str = "execute") -> None:
    """Per-row core callback, still bound to the server-owned running claim."""
    if mode not in {"preview", "execute"}:
        raise TargetCleanupServiceError("invalid-mode", status_code=400)
    with session_factory() as session:
        batch = _get_batch(session, batch_id)
        if getattr(batch, f"{mode}_status") != "running":
            raise TargetCleanupServiceError("batch-not-running")
        _require_running_job(session, batch, job_id, mode)
        _require_idle(session, ignore_job_id=job_id)
        _require_identity(batch)


def finish_preview(session_factory, batch_id: str, snapshot: dict[str, Any]) -> None:
    try:
        core.validate_snapshot(snapshot)
    except core.TargetCleanupError as error:
        raise TargetCleanupServiceError(error.code, error.summary) from error
    with _transaction(session_factory) as session:
        batch = _get_batch(session, batch_id)
        if batch.preview_status != "running":
            raise TargetCleanupServiceError("preview-not-running")
        _require_running_job(session, batch, batch.preview_job_id, "preview")
        if ({field: snapshot[field] for field in core.FROZEN_FIELDS} != _decode_json(batch.frozen_json)
                or snapshot["batch_id"] != batch.id or snapshot["months"] != batch.months
                or snapshot["store_id"] != batch.store_id or snapshot["shop_id"] != batch.shop_id):
            raise TargetCleanupServiceError("frozen-binding-mismatch")
        digest = core.payload_digest(snapshot)
        try:
            saved = core.read_snapshot(_verified_paths(batch)["snapshot"], digest)
            if saved != snapshot:
                raise TargetCleanupServiceError("snapshot-binding-mismatch")
        except (OSError, core.TargetCleanupError) as error:
            raise TargetCleanupServiceError("preview-artifact-invalid") from error
        candidates = [row for row in snapshot["rows"] if row["eligible"]]
        for position, row in enumerate(candidates):
            session.add(TargetCleanupItem(
                batch_id=batch.id, store_id=batch.store_id, invitation_id=row["invitation_id"],
                position=position, row_json=_json_text(row),
            ))
        batch.snapshot_sha256 = digest
        batch.scan_complete = snapshot["scan_complete"]
        batch.stop_reason = snapshot["stop_reason"]
        batch.pages_scanned = snapshot["pages_scanned"]
        batch.scan_count = len(snapshot["rows"])
        batch.candidate_count = len(candidates)
        batch.nonzero_count = sum(bool((row["accepted_count"] or 0) > 0 or (row["promoted_count"] or 0) > 0) for row in candidates)
        batch.preview_finished_at = core.parse_timestamp(snapshot["finished_at"])
        batch.expires_at = core.parse_timestamp(snapshot["expires_at"])
        batch.preview_status = "completed" if batch.scan_complete else "incomplete"


def _item_payload(item: TargetCleanupItem) -> dict[str, Any]:
    return {
        **_decode_json(item.row_json), "status": item.status, "action": item.action,
        "summary": item.summary,
        "attempted_at": item.attempted_at.isoformat() if item.attempted_at else "",
        "returned_at": item.returned_at.isoformat() if item.returned_at else "",
    }


def _validate_result(item: TargetCleanupItem, result: Any) -> tuple[datetime | None, datetime | None]:
    if not isinstance(result, dict) or set(result) != set(core.RESULT_FIELDS):
        raise TargetCleanupServiceError("invalid-item-result")
    try:
        row_json = _json_text({field: result[field] for field in core.ROW_FIELDS})
    except (TypeError, ValueError) as error:
        raise TargetCleanupServiceError("invalid-item-result") from error
    if row_json != item.row_json:
        raise TargetCleanupServiceError("result-evidence-mismatch")
    if not isinstance(result["status"], str) or result["status"] not in core.RESULT_STATUSES or not all(
        isinstance(result[field], str) for field in ("action", "summary", "attempted_at", "returned_at")
    ):
        raise TargetCleanupServiceError("invalid-item-result")
    try:
        attempted = core.parse_timestamp(result["attempted_at"]) if result["attempted_at"] else None
        returned = core.parse_timestamp(result["returned_at"]) if result["returned_at"] else None
    except core.TargetCleanupError as error:
        raise TargetCleanupServiceError("invalid-item-result") from error
    missing_write_ahead_time = result["status"] in {"attempting", "submitted", "uncertain"} and attempted is None
    unexpected_attempt_time = result["status"] in {"pending", "not_processed"} and attempted is not None
    reversed_times = attempted is not None and returned is not None and returned < attempted
    missing_submission_return_time = result["status"] == "submitted" and returned is None
    if missing_write_ahead_time or unexpected_attempt_time or reversed_times or missing_submission_return_time:
        raise TargetCleanupServiceError("invalid-item-result")
    return attempted, returned


def mark_item_attempting(session_factory, batch_id: str, invitation_id: str) -> None:
    """DB checkpoint callback, invoked AFTER the core's flushed JSON/CSV journal."""
    with _transaction(session_factory) as session:
        batch = _get_batch(session, batch_id)
        if batch.execute_status != "running":
            raise TargetCleanupServiceError("execution-not-running")
        _require_running_job(session, batch, batch.execute_job_id, "execute")
        item = session.scalar(select(TargetCleanupItem).where(
            TargetCleanupItem.batch_id == batch.id, TargetCleanupItem.invitation_id == invitation_id,
        ))
        if item is None or item.status != "pending":
            raise TargetCleanupServiceError("item-already-consumed")
        item.status = "attempting"
        item.attempted_at = _utc_now()


def _apply_result(item: TargetCleanupItem, result: dict[str, Any], *, recovering: bool = False) -> None:
    attempted, returned = _validate_result(item, result)
    # A stale journal cannot erase DB evidence, and uncertainty has no retry path.
    if item.status in {"submitted", "uncertain"}:
        if recovering or result["status"] == item.status:
            return
        raise TargetCleanupServiceError("protected-item-result")
    if item.status in TERMINAL_ITEM_STATUSES:
        if recovering:
            return
        if _item_payload(item) == result:
            return
        raise TargetCleanupServiceError("item-already-consumed")
    if recovering and item.status == "attempting" and result["status"] in {"pending", "not_processed", "attempting"}:
        item.status = "uncertain"
        item.summary = "Abandoned write attempt; manual platform review required."
        return
    if not recovering and result["status"] in {"submitted", "uncertain"} and item.status != "attempting":
        raise TargetCleanupServiceError("attempt-checkpoint-required")
    if item.status == "attempting" and attempted is None:
        raise TargetCleanupServiceError("attempt-evidence-required")
    item.status = result["status"]
    item.action = result["action"]
    item.summary = result["summary"]
    # The core's write-ahead timestamp precedes the DB callback by design.
    item.attempted_at = attempted or item.attempted_at
    item.returned_at = returned


def save_item_result(session_factory, batch_id: str, row_result: dict[str, Any]) -> None:
    with _transaction(session_factory) as session:
        batch = _get_batch(session, batch_id)
        if batch.execute_status != "running":
            raise TargetCleanupServiceError("execution-not-running")
        _require_running_job(session, batch, batch.execute_job_id, "execute")
        identifier = row_result.get("invitation_id") if isinstance(row_result, dict) else None
        item = session.scalar(select(TargetCleanupItem).where(
            TargetCleanupItem.batch_id == batch.id, TargetCleanupItem.invitation_id == identifier,
        ))
        if item is None:
            raise TargetCleanupServiceError("unknown-result-item")
        if not isinstance(row_result.get("status"), str) or row_result["status"] not in TERMINAL_ITEM_STATUSES:
            raise TargetCleanupServiceError("nonterminal-item-result")
        _apply_result(item, row_result)


def _read_results(batch: TargetCleanupBatch, items: list[TargetCleanupItem]) -> list[dict[str, Any]]:
    try:
        path = _verified_paths(batch)["results_json"]
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("oversized journal")
        payload = json.loads(path.read_bytes())
        if (not isinstance(payload, dict)
                or set(payload) != {"schema_version", "batch_id", "snapshot_sha256", "items"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != core.SCHEMA_VERSION
                or payload["batch_id"] != batch.id or payload["snapshot_sha256"] != batch.snapshot_sha256
                or not isinstance(payload["items"], list) or len(payload["items"]) != len(items)):
            raise ValueError("unbound journal")
        # Validate the ENTIRE file before accepting any result from it.
        for item, result in zip(items, payload["items"]):
            _validate_result(item, result)
        return payload["items"]
    except (OSError, ValueError, TypeError, KeyError, TargetCleanupServiceError) as error:
        raise TargetCleanupServiceError("result-journal-invalid") from error


def _close_execution(session, batch: TargetCleanupBatch, *, error: str = "") -> None:
    items = _items(session, batch.id)
    valid_journal = False
    journal_error = ""
    try:
        results = _read_results(batch, items)
        valid_journal = True
        for item, result in zip(items, results):
            _apply_result(item, result, recovering=True)
    except TargetCleanupServiceError as failure:
        journal_error = failure.code
    for item in items:
        if item.status == "attempting" or (item.status == "pending" and not valid_journal):
            item.status = "uncertain"
            item.summary = "Execution evidence missing or abandoned; manual platform review required."
            item.attempted_at = item.attempted_at or batch.execute_started_at or _utc_now()
        elif item.status == "pending":
            item.status = "not_processed"
            item.summary = "Execution stopped before processing this item."
    statuses = {item.status for item in items}
    if "uncertain" in statuses:
        batch.execute_status = "needs_review"
    elif items and statuses == {"submitted"}:
        batch.execute_status = "completed"
    else:
        batch.execute_status = "partial"
    batch.execute_finished_at = _utc_now()
    batch.error_code = journal_error or ("execution-incomplete" if error or batch.execute_status != "completed" else "")
    batch.error_summary = error or journal_error


def _close_unstarted_execution(session, batch: TargetCleanupBatch, error: str) -> None:
    for item in _items(session, batch.id):
        if item.status == "pending":
            item.status = "not_processed"
            item.summary = "Execution never started."
        elif item.status == "attempting":
            item.status = "uncertain"
            item.summary = "Unexpected attempt evidence; manual review required."
    statuses = {item.status for item in _items(session, batch.id)}
    batch.execute_status = "needs_review" if "uncertain" in statuses else "partial"
    batch.execute_finished_at = _utc_now()
    batch.error_code = "execution-not-started"
    batch.error_summary = error


def _result_csv_is_current(path: Path, expected_content: bytes) -> bool:
    try:
        return (not path.is_symlink() and path.stat().st_size == len(expected_content)
                and path.read_bytes() == expected_content)
    except OSError:
        return False


def _refresh_result_csv(session_factory, batch_id: str) -> None:
    """Rebuild only the derived report, AFTER recovered evidence is committed."""
    try:
        with session_factory() as session:
            batch = _get_batch(session, batch_id)
            if batch.execute_status not in {"completed", "partial", "needs_review"}:
                return
            path = _verified_paths(batch)["results_csv"]
            items = [_item_payload(item) for item in _items(session, batch.id)]
            if _result_csv_is_current(path, core.create_csv_bytes(items, results=True)):
                return
        core.write_csv_report(path, items, results=True)
    except (OSError, core.TargetCleanupError, TargetCleanupServiceError):
        # Do not roll back recovery or expose an obsolete export as a final report.
        logger.warning("Cleanup result CSV unavailable; retained database evidence: batch=%s", batch_id)


def _recover_terminal_batch(session, batch: TargetCleanupBatch) -> None:
    if batch.preview_status in {"queued", "running"}:
        job = session.get(Job, batch.preview_job_id)
        if job is None or job.status not in ACTIVE_JOB_STATUSES:
            batch.preview_status = "failed"
            batch.error_code = "preview-abandoned"
            batch.error_summary = "Preview never completed; no executable snapshot was accepted."
    if batch.execute_status not in {"queued", "running"}:
        return
    job = session.get(Job, batch.execute_job_id)
    if job is not None and job.status in ACTIVE_JOB_STATUSES:
        return
    if batch.execute_status == "queued":
        _close_unstarted_execution(session, batch, "Queued execution was cancelled or abandoned before its DB claim.")
    else:
        _close_execution(session, batch, error="Execution worker terminated; recovered without retry.")


def recover_batch(session_factory, batch_id: str) -> None:
    """Recover only terminal jobs; active work is never inferred abandoned."""
    with _transaction(session_factory) as session:
        _recover_terminal_batch(session, _get_batch(session, batch_id))
    _refresh_result_csv(session_factory, batch_id)


def finish_execution(session_factory, batch_id: str, status: str, error: str = "") -> None:
    """Caller status is diagnostic; actual item evidence determines the outcome."""
    if status not in {"completed", "partial", "needs_review", "failed", "cancelled", "interrupted"}:
        raise TargetCleanupServiceError("invalid-execution-status", status_code=400)
    with _transaction(session_factory) as session:
        batch = _get_batch(session, batch_id)
        if batch.execute_status not in {"running", "queued"}:
            return
        if batch.execute_status == "queued":
            _close_unstarted_execution(session, batch, error or status)
        else:
            _close_execution(session, batch, error=error)
    _refresh_result_csv(session_factory, batch_id)


def fail_batch(session_factory, batch_id: str, mode: str, error: str, *, code: str = "script-launch-failed") -> None:
    """Handler failure hook, including exceptions before a subprocess starts."""
    if mode not in {"preview", "execute"}:
        raise TargetCleanupServiceError("invalid-mode", status_code=400)
    with _transaction(session_factory) as session:
        batch = _get_batch(session, batch_id)
        if mode == "preview" and batch.preview_status in {"queued", "running"}:
            batch.preview_status = "failed"
            batch.error_code, batch.error_summary = code, error
        elif mode == "execute" and batch.execute_status == "queued":
            _close_unstarted_execution(session, batch, error)
            batch.error_code = code
        elif mode == "execute" and batch.execute_status == "running":
            _close_execution(session, batch, error=error)
    _refresh_result_csv(session_factory, batch_id)


def _pagination(offset: int, limit: int, maximum: int) -> tuple[int, int]:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= maximum:
        raise TargetCleanupServiceError("invalid-pagination", status_code=400)
    return offset, limit


def _cached_execute_reason(session, batch: TargetCleanupBatch) -> str:
    if batch.execute_status != "unclaimed":
        return "batch-already-consumed"
    if batch.preview_status != "completed":
        return "incomplete-preview" if batch.preview_status == "incomplete" else "preview-not-completed"
    if not batch.scan_complete:
        return "incomplete-preview"
    if not batch.candidate_count:
        return "empty-preview"
    current_time = _utc_now()
    if (batch.expires_at is None or batch.preview_finished_at is None
            or current_time < batch.preview_finished_at or current_time >= batch.expires_at):
        return "expired-preview"
    frozen = _decode_json(batch.frozen_json)
    if not isinstance(frozen, dict) or frozen.get("run_date") != datetime.now().astimezone().date().isoformat():
        return "different-local-day"
    candidate_ids = select(TargetCleanupItem.invitation_id).where(TargetCleanupItem.batch_id == batch.id)
    blocked = session.scalar(select(TargetCleanupItem.id).where(
        TargetCleanupItem.store_id == batch.store_id, TargetCleanupItem.batch_id != batch.id,
        TargetCleanupItem.invitation_id.in_(candidate_ids), TargetCleanupItem.status.in_(BLOCKED_ITEM_STATUSES),
    ).limit(1))
    if blocked is not None:
        return "historical-write-requires-review"
    try:
        _require_idle(session)
    except TargetCleanupServiceError as error:
        return error.code
    return ""


def _batch_summary(batch: TargetCleanupBatch) -> dict[str, Any]:
    frozen = _decode_json(batch.frozen_json) if batch.frozen_json else None
    return {
        "batch_id": batch.id, "months": batch.months,
        "store_id": batch.store_id, "store_name": batch.store_name,
        "shop_id": batch.shop_id, "shop_region": batch.shop_region, "frozen": frozen,
        "preview_status": batch.preview_status, "execute_status": batch.execute_status,
        "preview_job_id": batch.preview_job_id, "execute_job_id": batch.execute_job_id,
        "scan_complete": batch.scan_complete, "stop_reason": batch.stop_reason,
        "pages_scanned": batch.pages_scanned, "scan_count": batch.scan_count,
        "candidate_count": batch.candidate_count, "nonzero_count": batch.nonzero_count,
        "snapshot_sha256": batch.snapshot_sha256, "error_code": batch.error_code,
        "error_summary": batch.error_summary,
        **{field: getattr(batch, field).isoformat() if getattr(batch, field) else "" for field in (
            "created_at", "preview_started_at", "preview_finished_at", "expires_at",
            "confirmed_at", "execute_started_at", "execute_finished_at",
        )},
    }


def list_batches(session_factory, offset: int = 0, limit: int = 20, idempotency_key: str = "") -> dict[str, Any]:
    offset, limit = _pagination(offset, limit, 20)
    with session_factory() as session:
        conditions = []
        if idempotency_key:
            key = _request_key(idempotency_key)
            conditions.append(or_(
                TargetCleanupBatch.preview_idempotency_key == key,
                TargetCleanupBatch.execute_idempotency_key == key,
            ))
        total = session.scalar(select(func.count()).select_from(TargetCleanupBatch).where(*conditions))
        batches = session.scalars(select(TargetCleanupBatch).where(*conditions).order_by(
            TargetCleanupBatch.created_at.desc(), TargetCleanupBatch.id,
        ).offset(offset).limit(limit)).all()
        return {"batches": [_batch_summary(batch) for batch in batches], "total": total, "offset": offset, "limit": limit}


def batch_payload(session_factory, batch_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    offset, limit = _pagination(offset, limit, 100)
    with session_factory() as session:
        batch = _get_batch(session, batch_id)
        items = session.scalars(select(TargetCleanupItem).where(TargetCleanupItem.batch_id == batch.id).order_by(
            TargetCleanupItem.position,
        ).offset(offset).limit(limit)).all()
        counts = dict(session.execute(select(TargetCleanupItem.status, func.count()).where(
            TargetCleanupItem.batch_id == batch.id,
        ).group_by(TargetCleanupItem.status)).all())
        reason = _cached_execute_reason(session, batch)
        paths = _verified_paths(batch)
        results_csv_ready = paths["results_csv"].is_file()
        if batch.execute_status in {"completed", "partial", "needs_review"}:
            expected_report = core.create_csv_bytes([_item_payload(item) for item in _items(session, batch.id)], results=True)
            results_csv_ready = _result_csv_is_current(paths["results_csv"], expected_report)
        downloads = {
            name: "/api/exports/download?" + urlencode({"filename": "target_cleanup/" + path.name})
            for name, path in paths.items() if path.suffix == ".csv" and path.is_file()
            and (name != "results_csv" or results_csv_ready)
        }
        job_states = {}
        for phase in ("preview", "execute"):
            job_id = getattr(batch, f"{phase}_job_id")
            job = session.get(Job, job_id) if job_id else None
            job_states[phase] = {"job_id": job_id, "status": job.status if job else ""}
        return {
            **_batch_summary(batch), "items": [_item_payload(item) for item in items],
            "counts": {status: counts.get(status, 0) for status in sorted(core.RESULT_STATUSES)},
            "offset": offset, "limit": limit, "total": sum(counts.values()),
            "can_execute": not reason, "execute_block_reason": reason,
            "identity_requires_recheck": True, "jobs": job_states, "downloads": downloads,
            "results_csv_ready": results_csv_ready,
        }
