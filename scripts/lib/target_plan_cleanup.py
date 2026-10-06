"""Fixed natural-month rules and server-owned target-plan cleanup artifacts.

The migration source is zn_daren 2d8e21c plus its 2026-10-05 worktree.
Only the two date presets are supported; accepted/promoted counts are evidence,
not exclusion criteria. CSV reports are never execution inputs.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
IMPLEMENTATION_VERSION = "target-cleanup-v2"
PREVIEW_VALIDITY_SECONDS = 30 * 60
MAX_SCAN_ROWS = 5000
ROW_FIELDS = (
    "invitation_id", "name", "last_modified", "modified_date", "invited_count",
    "accepted_count", "promoted_count", "row_key", "eligible", "reason",
)
RESULT_FIELDS = (*ROW_FIELDS, "status", "action", "summary", "attempted_at", "returned_at")
FROZEN_FIELDS = (
    "schema_version", "implementation_version", "batch_id", "store_id", "shop_id",
    "months", "run_date", "started_at", "timezone_name", "offset_minutes", "cutoff",
)
SNAPSHOT_FIELDS = (
    *FROZEN_FIELDS, "finished_at", "expires_at", "scan_complete", "stop_reason",
    "pages_scanned", "rows", "candidate_ids",
)
RESULT_STATUSES = frozenset({
    "pending", "attempting", "submitted", "skipped", "failed", "uncertain", "not_processed",
})


class TargetCleanupError(RuntimeError):
    def __init__(self, code: str, summary: str = "") -> None:
        self.code = code
        self.summary = summary or code
        super().__init__(self.summary)


def validate_months(months: Any) -> int:
    if type(months) is not int or months not in (2, 4):
        raise TargetCleanupError("invalid-months", "Only the 2/4 natural-month presets are allowed.")
    return months


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def payload_digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def parse_timestamp(value: Any) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            raise ValueError("timezone required")
        return timestamp.astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise TargetCleanupError("invalid-timestamp") from error


def freeze_rule(*, batch_id: str, store_id: str, shop_id: str, months: int,
                started_at: datetime | None = None) -> dict[str, Any]:
    from .target_invitation_dom import months_ago

    validate_months(months)
    local_time = (started_at or datetime.now().astimezone()).astimezone()
    if str(uuid.UUID(batch_id)) != batch_id or not store_id or not shop_id:
        raise TargetCleanupError("invalid-identity")
    return {
        "schema_version": SCHEMA_VERSION,
        "implementation_version": IMPLEMENTATION_VERSION,
        "batch_id": batch_id, "store_id": store_id, "shop_id": shop_id,
        "months": months, "run_date": local_time.date().isoformat(),
        "started_at": local_time.astimezone(timezone.utc).isoformat(),
        "timezone_name": local_time.tzname() or "local",
        "offset_minutes": int(local_time.utcoffset().total_seconds() // 60),
        "cutoff": months_ago(local_time.date(), months).isoformat(),
    }


def evaluate_scan_row(row: dict[str, Any], cutoff: date) -> dict[str, Any]:
    from .target_invitation_dom import parse_ui_date

    def parse_count(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(str(value).strip())
        except ValueError:
            return None

    invitation_id = str(row.get("invitation_id") or "").strip()
    modified_text = str(row.get("last_modified") or "").strip()
    modified_date = parse_ui_date(modified_text)
    reason = (
        "missing-invitation-id" if not invitation_id else
        "unparsed-modified-date" if modified_date is None else
        "before-cutoff" if modified_date < cutoff else "not-before-cutoff"
    )
    return {
        "invitation_id": invitation_id, "name": str(row.get("name") or ""),
        "last_modified": modified_text,
        "modified_date": modified_date.isoformat() if modified_date else "",
        "invited_count": parse_count(row.get("invited_count")),
        "accepted_count": parse_count(row.get("accepted_count")),
        "promoted_count": parse_count(row.get("promoted_count")),
        "row_key": str(row.get("row_key") or ""),
        "eligible": reason == "before-cutoff", "reason": reason,
    }


def create_snapshot(frozen: dict[str, Any], scan: dict[str, Any], *,
                    finished_at: datetime | None = None) -> dict[str, Any]:
    cutoff = date.fromisoformat(frozen["cutoff"])
    rows = [evaluate_scan_row(row, cutoff) for row in scan["rows"]]
    complete = scan.get("scan_complete") is True
    stop_reason = str(scan.get("stop_reason") or "unknown")
    if any(not row["modified_date"] for row in rows):
        complete, stop_reason = False, "unparsed-modified-date"
    finished = finished_at or datetime.now(timezone.utc)
    snapshot = {
        **frozen, "finished_at": finished.isoformat(),
        "expires_at": (finished + timedelta(seconds=PREVIEW_VALIDITY_SECONDS)).isoformat(),
        "scan_complete": complete, "stop_reason": stop_reason,
        "pages_scanned": scan["pages_scanned"], "rows": rows,
        "candidate_ids": [row["invitation_id"] for row in rows if row["eligible"]],
    }
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: Any) -> dict[str, Any]:
    from .target_invitation_dom import months_ago

    try:
        if not isinstance(snapshot, dict) or set(snapshot) != set(SNAPSHOT_FIELDS):
            raise ValueError("snapshot fields")
        if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != SCHEMA_VERSION:
            raise ValueError("schema version")
        if snapshot["implementation_version"] != IMPLEMENTATION_VERSION:
            raise ValueError("implementation version")
        validate_months(snapshot["months"])
        if str(uuid.UUID(snapshot["batch_id"])) != snapshot["batch_id"]:
            raise ValueError("batch id")
        for field in ("store_id", "shop_id", "timezone_name", "stop_reason"):
            if not isinstance(snapshot[field], str) or not snapshot[field].strip():
                raise ValueError(field)
        if type(snapshot["offset_minutes"]) is not int or not -840 <= snapshot["offset_minutes"] <= 840:
            raise ValueError("offset")
        run_date = date.fromisoformat(snapshot["run_date"])
        cutoff = months_ago(run_date, snapshot["months"])
        if cutoff.isoformat() != snapshot["cutoff"]:
            raise ValueError("cutoff")
        started = parse_timestamp(snapshot["started_at"])
        if (started + timedelta(minutes=snapshot["offset_minutes"])).date() != run_date:
            raise ValueError("local run date")
        finished = parse_timestamp(snapshot["finished_at"])
        expires = parse_timestamp(snapshot["expires_at"])
        if finished < started or expires != finished + timedelta(seconds=PREVIEW_VALIDITY_SECONDS):
            raise ValueError("preview timestamps")
        if type(snapshot["scan_complete"]) is not bool:
            raise ValueError("completeness")
        pages = snapshot["pages_scanned"]
        if type(pages) is not int or not 0 <= pages <= 50:
            raise ValueError("page count")
        rows = snapshot["rows"]
        if not isinstance(rows, list) or len(rows) > MAX_SCAN_ROWS:
            raise ValueError("row count")
        identifiers: set[str] = set()
        candidates: list[str] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(ROW_FIELDS):
                raise ValueError("row fields")
            for field in ("invitation_id", "name", "last_modified", "modified_date", "row_key", "reason"):
                if not isinstance(row[field], str):
                    raise ValueError("row text")
            for field in ("invited_count", "accepted_count", "promoted_count"):
                if row[field] is not None and (type(row[field]) is not int or row[field] < 0):
                    raise ValueError("row count")
            if type(row["eligible"]) is not bool or evaluate_scan_row(row, cutoff) != row:
                raise ValueError("row evidence")
            identifier = row["invitation_id"]
            if identifier:
                if identifier in identifiers or len(identifier) > 200:
                    raise ValueError("duplicate or invalid id")
                identifiers.add(identifier)
            if row["eligible"]:
                candidates.append(identifier)
        if snapshot["candidate_ids"] != candidates:
            raise ValueError("candidate scope")
        if snapshot["scan_complete"] and (
            any(not row["modified_date"] for row in rows)
            or snapshot["stop_reason"] not in {"date-boundary", "first-page", "empty-list"}
        ):
            raise ValueError("completion evidence")
    except (KeyError, TypeError, ValueError) as error:
        raise TargetCleanupError("invalid-snapshot", "The server preview evidence is invalid.") from error
    return snapshot


def validate_freshness(snapshot: dict[str, Any], *, now: datetime | None = None,
                       local_date: date | None = None) -> None:
    validate_snapshot(snapshot)
    current_time = now or datetime.now(timezone.utc)
    current_date = local_date or datetime.now().astimezone().date()
    if not snapshot["scan_complete"]:
        raise TargetCleanupError("incomplete-preview")
    if not snapshot["candidate_ids"]:
        raise TargetCleanupError("empty-preview")
    if current_time < parse_timestamp(snapshot["finished_at"]) or current_time >= parse_timestamp(snapshot["expires_at"]):
        raise TargetCleanupError("expired-preview")
    if current_date.isoformat() != snapshot["run_date"]:
        raise TargetCleanupError("different-local-day")


def atomic_write_bytes(path: Path, content: bytes, *, immutable: bool = False) -> None:
    """Flush a private checkpoint before exposing it; failed writes never click."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise TargetCleanupError("unsafe-artifact-path")
    temporary_path = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            # A hard link publishes a fully flushed file without replacing an old snapshot.
            os.link(temporary_path, path)
        else:
            temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Any, *, immutable: bool = False) -> None:
    atomic_write_bytes(path, canonical_json(payload), immutable=immutable)


def create_csv_bytes(rows: list[dict[str, Any]], *, results: bool = False) -> bytes:
    import io

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS if results else ROW_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def write_csv_report(path: Path, rows: list[dict[str, Any]], *, results: bool = False,
                     immutable: bool = False) -> None:
    atomic_write_bytes(path, create_csv_bytes(rows, results=results), immutable=immutable)


def read_snapshot(path: Path, expected_digest: str) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise TargetCleanupError("invalid-snapshot-path")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_digest:
        raise TargetCleanupError("snapshot-hash-mismatch")
    try:
        snapshot = json.loads(content)
    except ValueError as error:
        raise TargetCleanupError("invalid-snapshot") from error
    return validate_snapshot(snapshot)


def artifact_paths(directory: Path, batch_id: str) -> dict[str, Path]:
    try:
        if str(uuid.UUID(batch_id)) != batch_id:
            raise ValueError("noncanonical batch id")
    except (ValueError, TypeError, AttributeError) as error:
        raise TargetCleanupError("invalid-batch-id") from error
    suffixes = {
        "scan_csv": "_scan.csv", "candidates_csv": "_candidates.csv",
        "snapshot": "_snapshot.json", "results_json": "_results.json",
        "results_csv": "_results.csv", "backup_json": "_pre_execute.json",
        "backup_csv": "_pre_execute.csv", "execute_envelope": "_execute.json",
    }
    return {key: directory / (batch_id + suffix) for key, suffix in suffixes.items()}


def persist_preview(snapshot: dict[str, Any], directory: Path) -> str:
    validate_snapshot(snapshot)
    paths = artifact_paths(directory, snapshot["batch_id"])
    write_csv_report(paths["scan_csv"], snapshot["rows"], immutable=True)
    write_csv_report(paths["candidates_csv"], [row for row in snapshot["rows"] if row["eligible"]], immutable=True)
    atomic_write_json(paths["snapshot"], snapshot, immutable=True)
    return payload_digest(snapshot)


def read_execution_results(path: Path, snapshot: dict[str, Any], expected_digest: str) -> list[dict[str, Any]]:
    """Only accept checkpoints bound to the original evidence and complete ID set."""
    if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
        raise TargetCleanupError("invalid-results-path")
    try:
        report = json.loads(path.read_bytes())
        if not isinstance(report, dict) or set(report) != {"schema_version", "batch_id", "snapshot_sha256", "items"}:
            raise ValueError("result fields")
        if type(report["schema_version"]) is not int or report["schema_version"] != SCHEMA_VERSION:
            raise ValueError("result version")
        if report["batch_id"] != snapshot["batch_id"] or report["snapshot_sha256"] != expected_digest:
            raise ValueError("result binding")
        items = report["items"]
        candidates = [row for row in snapshot["rows"] if row["eligible"]]
        if not isinstance(items, list) or len(items) != len(candidates):
            raise ValueError("result count")
        for item, candidate in zip(items, candidates, strict=True):
            if not isinstance(item, dict) or set(item) != set(RESULT_FIELDS):
                raise ValueError("result item fields")
            if any(item[field] != candidate[field] for field in ROW_FIELDS):
                raise ValueError("result evidence changed")
            if item["status"] not in RESULT_STATUSES:
                raise ValueError("result status")
            for field in ("action", "summary", "attempted_at", "returned_at"):
                if not isinstance(item[field], str):
                    raise ValueError("result text")
            for field in ("attempted_at", "returned_at"):
                if item[field]:
                    parse_timestamp(item[field])
            if item["status"] in {"attempting", "submitted", "uncertain"} and not item["attempted_at"]:
                raise ValueError("missing write-ahead time")
        return items
    except (ValueError, KeyError, TypeError) as error:
        raise TargetCleanupError("invalid-results") from error


def execute_snapshot(
    snapshot: dict[str, Any], *, directory: Path, snapshot_sha256: str,
    execute: bool = False, yes: bool = False,
    before_attempt: Callable[[str], None] | None = None,
    on_result: Callable[[dict[str, Any]], None] | None = None,
    validate_identity: Callable[[], None] | None = None,
    locate_fn: Callable[..., dict[str, Any]] | None = None,
    cancel_fn: Callable[..., dict[str, Any]] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Consume only a server-claimed snapshot, with a write-ahead barrier per ID.

    Persistence errors are outside UI retry handling. If one write is uncertain,
    all remaining items stop; neither a refresh nor a new request retries it.
    """
    from .target_invitation_dom import cancel_invitation_by_id, locate_target_invitation

    if execute is not True or yes is not True:
        raise TargetCleanupError("execute-confirmation-required", "Execution requires both --execute and --yes.")
    validate_snapshot(snapshot)
    if payload_digest(snapshot) != snapshot_sha256:
        raise TargetCleanupError("snapshot-hash-mismatch")
    validate_freshness(snapshot)
    if before_attempt is None or on_result is None or validate_identity is None:
        raise TargetCleanupError("server-claim-required")
    validate_identity()
    paths = artifact_paths(directory, snapshot["batch_id"])
    items = [{**row, "status": "pending", "action": "pending", "summary": "",
              "attempted_at": "", "returned_at": ""}
             for row in snapshot["rows"] if row["eligible"]]
    report = {"schema_version": SCHEMA_VERSION, "batch_id": snapshot["batch_id"],
              "snapshot_sha256": snapshot_sha256, "items": items}
    atomic_write_json(paths["backup_json"], snapshot, immutable=True)
    write_csv_report(paths["backup_csv"], items, results=True, immutable=True)

    def save_checkpoint() -> None:
        # JSON remains authoritative if the derived CSV update fails.
        atomic_write_json(paths["results_json"], report)
        write_csv_report(paths["results_csv"], items, results=True)

    save_checkpoint()
    locate = locate_fn or locate_target_invitation
    cancel = cancel_fn or cancel_invitation_by_id
    cutoff = date.fromisoformat(snapshot["cutoff"])
    interrupted = False
    try:
        for item in items:
            validate_identity()
            parameters = {
                "invitation_id": item["invitation_id"],
                "expected_last_modified": item["last_modified"], "cutoff": cutoff,
            }
            try:
                location = locate(snapshot["store_id"], **parameters)
            except Exception:
                location = {"revalidated": False, "reason": "location-error"}
            if location.get("revalidated") is not True:
                item.update(status="skipped", action="not-clicked", summary=str(location.get("reason") or "unverified-target"),
                            returned_at=datetime.now(timezone.utc).isoformat())
                save_checkpoint()
                on_result(dict(item))
                continue
            # Recheck running-store identity immediately before the write barrier.
            validate_identity()
            item.update(status="attempting", action="attempting", attempted_at=datetime.now(timezone.utc).isoformat())
            save_checkpoint()
            before_attempt(item["invitation_id"])
            try:
                outcome = cancel(snapshot["store_id"], **parameters, execute=True)
            except Exception:
                outcome = {"status": "uncertain", "action": "cancel:unknown", "reason": "write-or-readback-error"}
            if not isinstance(outcome, dict):
                outcome = {"status": "uncertain", "action": "cancel:unknown", "reason": "invalid-write-response"}
            status = outcome.get("status")
            if status not in {"submitted", "skipped", "failed", "uncertain"}:
                status = "uncertain"
            item.update(status=status, action=str(outcome.get("action") or "cancel:unknown"),
                        summary=str(outcome.get("reason") or status), returned_at=datetime.now(timezone.utc).isoformat())
            save_checkpoint()
            on_result(dict(item))
            if status == "uncertain":
                interrupted = True
                break
            (sleep_fn or time.sleep)(1.2)
    except BaseException:
        interrupted = True
        # Preserve attempting when persistence has failed. Recovery treats it as
        # uncertain, even if the preceding failure happened before the click.
        raise
    finally:
        if interrupted:
            for item in items:
                if item["status"] == "pending":
                    item.update(status="not_processed", action="not-processed", summary="batch-stopped")
            # A failed final flush must propagate, not silently permit more writes.
            save_checkpoint()
    counts = {status: sum(item["status"] == status for item in items) for status in RESULT_STATUSES}
    batch_status = "completed" if counts["submitted"] == len(items) else (
        "needs_review" if counts["uncertain"] or counts["attempting"] else "partial"
    )
    return {"status": batch_status, "counts": counts, "items": items}
