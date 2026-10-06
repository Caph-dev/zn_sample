"""Offline rule/evidence boundaries for the two fixed cleanup presets."""
from __future__ import annotations

import copy
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib import target_plan_cleanup as cleanup


def make_snapshot(*, months=4, rows=None, complete=True):
    started = datetime.now().astimezone()
    frozen = cleanup.freeze_rule(batch_id=str(uuid.uuid4()), store_id="store-1",
                                 shop_id="shop-1", months=months, started_at=started)
    return cleanup.create_snapshot(frozen, {
        "rows": rows if rows is not None else [{
            "invitation_id": "old-1", "name": "Old plan", "last_modified": "2020/01/01",
            "accepted_count": 3, "promoted_count": 2,
        }],
        "scan_complete": complete, "stop_reason": "first-page" if complete else "page-limit",
        "pages_scanned": 1,
    })


@pytest.mark.parametrize("months", [2, 4])
def test_cutoff_is_exclusive_and_counts_do_not_exclude(months):
    from lib.target_invitation_dom import months_ago

    cutoff = months_ago(date(2026, 10, 5), months)
    older = cleanup.evaluate_scan_row({"invitation_id": "old", "last_modified":
        (cutoff - timedelta(days=1)).isoformat(), "accepted_count": 9, "promoted_count": 4}, cutoff)
    boundary = cleanup.evaluate_scan_row({"invitation_id": "edge", "last_modified": cutoff.isoformat()}, cutoff)
    assert older["eligible"] is True
    assert older["accepted_count"] == 9
    assert boundary["eligible"] is False


@pytest.mark.parametrize("months", [True, "2", 2.0, 0, 1, 3, 5, None])
def test_presets_reject_arbitrary_months(months):
    with pytest.raises(cleanup.TargetCleanupError, match="natural-month"):
        cleanup.validate_months(months)


def test_missing_id_and_unparsed_dates_are_not_candidates():
    cutoff = date(2026, 6, 5)
    assert cleanup.evaluate_scan_row({"row_key": "123", "last_modified": "2020/01/01"}, cutoff)["reason"] == "missing-invitation-id"
    snapshot = make_snapshot(rows=[{"invitation_id": "unknown", "last_modified": "unreadable"}])
    assert not snapshot["scan_complete"]
    assert snapshot["candidate_ids"] == []


@pytest.mark.parametrize("mutation", [
    lambda snapshot: snapshot.update(months=3),
    lambda snapshot: snapshot.update(cutoff="2000-01-01"),
    lambda snapshot: snapshot.update(candidate_ids=["other"]),
    lambda snapshot: snapshot.update(extra="unknown"),
    lambda snapshot: snapshot["rows"][0].update(eligible=False),
    lambda snapshot: snapshot["rows"].append(copy.deepcopy(snapshot["rows"][0])),
    lambda snapshot: snapshot.update(stop_reason="page-limit"),
])
def test_evidence_schema_and_candidate_scope_are_recomputed(mutation):
    snapshot = make_snapshot()
    mutation(snapshot)
    with pytest.raises(cleanup.TargetCleanupError):
        cleanup.validate_snapshot(snapshot)


def test_preview_validity_begins_at_completion_and_requires_same_local_day():
    snapshot = make_snapshot()
    finished = cleanup.parse_timestamp(snapshot["finished_at"])
    local_day = date.fromisoformat(snapshot["run_date"])
    cleanup.validate_freshness(snapshot, now=finished + timedelta(minutes=29), local_date=local_day)
    with pytest.raises(cleanup.TargetCleanupError, match="expired-preview"):
        cleanup.validate_freshness(snapshot, now=finished + timedelta(minutes=30), local_date=local_day)
    with pytest.raises(cleanup.TargetCleanupError, match="different-local-day"):
        cleanup.validate_freshness(snapshot, now=finished, local_date=local_day + timedelta(days=1))


def test_snapshot_is_immutable_and_hash_covers_exact_bytes(tmp_path):
    snapshot = make_snapshot()
    snapshot_path = tmp_path / "snapshot.json"
    cleanup.atomic_write_json(snapshot_path, snapshot, immutable=True)
    assert cleanup.read_snapshot(snapshot_path, cleanup.payload_digest(snapshot)) == snapshot
    with pytest.raises(FileExistsError):
        cleanup.atomic_write_json(snapshot_path, snapshot, immutable=True)
    snapshot_path.write_bytes(snapshot_path.read_bytes() + b" ")
    with pytest.raises(cleanup.TargetCleanupError, match="snapshot-hash-mismatch"):
        cleanup.read_snapshot(snapshot_path, cleanup.payload_digest(snapshot))


def execution_callbacks():
    return {
        "before_attempt": Mock(), "on_result": Mock(), "validate_identity": Mock(),
        "locate_fn": Mock(return_value={"revalidated": True}),
        "cancel_fn": Mock(return_value={"status": "submitted", "action": "cancel:synthetic", "reason": "operation-submitted"}),
        "sleep_fn": Mock(),
    }


def execute_fixture(snapshot, directory, callbacks, **overrides):
    arguments = {"directory": directory, "snapshot_sha256": cleanup.payload_digest(snapshot),
                 "execute": True, "yes": True, **callbacks, **overrides}
    return cleanup.execute_snapshot(snapshot, **arguments)


def test_fresh_v1_preview_with_valid_digest_cannot_execute_or_be_resigned(tmp_path, monkeypatch):
    assert cleanup.IMPLEMENTATION_VERSION == "target-cleanup-v2"
    with monkeypatch.context() as legacy_implementation:
        legacy_implementation.setattr(cleanup, "IMPLEMENTATION_VERSION", "target-cleanup-v1")
        snapshot = make_snapshot()
        digest = cleanup.persist_preview(snapshot, tmp_path)
        cleanup.validate_freshness(snapshot)
    original_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    snapshot_path = cleanup.artifact_paths(tmp_path, snapshot["batch_id"])["snapshot"]
    assert digest == cleanup.payload_digest(snapshot)
    assert cleanup.parse_timestamp(snapshot["expires_at"]) > datetime.now(timezone.utc)
    for validate in (cleanup.validate_snapshot, cleanup.validate_freshness):
        with pytest.raises(cleanup.TargetCleanupError) as error:
            validate(snapshot)
        assert error.value.code == "invalid-snapshot"
    with pytest.raises(cleanup.TargetCleanupError) as error:
        cleanup.read_snapshot(snapshot_path, digest)
    assert error.value.code == "invalid-snapshot"
    callbacks = execution_callbacks()
    with pytest.raises(cleanup.TargetCleanupError) as error:
        execute_fixture(snapshot, tmp_path, callbacks)
    assert error.value.code == "invalid-snapshot"
    for callback in callbacks.values():
        callback.assert_not_called()
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original_files


@pytest.mark.parametrize("flags", [(False, False), (True, False), (False, True)])
def test_missing_execution_gate_has_zero_platform_calls(tmp_path, flags):
    snapshot = make_snapshot()
    callbacks = execution_callbacks()
    with pytest.raises(cleanup.TargetCleanupError, match="both --execute and --yes"):
        execute_fixture(snapshot, tmp_path, callbacks, execute=flags[0], yes=flags[1])
    callbacks["locate_fn"].assert_not_called()
    callbacks["cancel_fn"].assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_fixed_snapshot_all_candidates_and_write_ahead_order(tmp_path):
    snapshot = make_snapshot(rows=[{"invitation_id": identifier, "last_modified": "2020/01/01"}
                                   for identifier in ("first", "second")])
    callbacks = execution_callbacks()
    paths = cleanup.artifact_paths(tmp_path, snapshot["batch_id"])

    def assert_before_click(store_id, **parameters):
        checkpoint = cleanup.read_execution_results(paths["results_json"], snapshot, cleanup.payload_digest(snapshot))
        current = next(item for item in checkpoint if item["invitation_id"] == parameters["invitation_id"])
        assert current["status"] == "attempting"
        assert callbacks["before_attempt"].call_args.args == (parameters["invitation_id"],)
        assert paths["backup_json"].is_file() and paths["backup_csv"].is_file()
        assert store_id == snapshot["store_id"]
        return {"status": "submitted", "action": "cancel:synthetic", "reason": "operation-submitted"}

    callbacks["cancel_fn"].side_effect = assert_before_click
    report = execute_fixture(snapshot, tmp_path, callbacks)
    assert report["status"] == "completed"
    assert report["counts"]["submitted"] == 2
    assert callbacks["cancel_fn"].call_count == 2
    assert {call.kwargs["invitation_id"] for call in callbacks["cancel_fn"].call_args_list} == {"first", "second"}


def test_changed_or_unverified_target_is_not_clicked(tmp_path):
    snapshot = make_snapshot()
    callbacks = execution_callbacks()
    callbacks["locate_fn"].return_value = {"revalidated": False, "reason": "last-modified-changed"}
    report = execute_fixture(snapshot, tmp_path, callbacks)
    assert report["status"] == "partial"
    assert report["items"][0]["status"] == "skipped"
    callbacks["before_attempt"].assert_not_called()
    callbacks["cancel_fn"].assert_not_called()


def test_connection_loss_after_click_is_never_retried(tmp_path):
    snapshot = make_snapshot(rows=[{"invitation_id": identifier, "last_modified": "2020/01/01"}
                                   for identifier in ("first", "second")])
    callbacks = execution_callbacks()
    callbacks["cancel_fn"].side_effect = TimeoutError("Synthetic click response lost")
    report = execute_fixture(snapshot, tmp_path, callbacks)
    assert report["status"] == "needs_review"
    assert [item["status"] for item in report["items"]] == ["uncertain", "not_processed"]
    callbacks["cancel_fn"].assert_called_once()
    callbacks["locate_fn"].assert_called_once()


@pytest.mark.parametrize("failed_write", ["backup", "initial-csv", "attempting-csv", "claim"])
def test_persistence_failure_before_click_stops_execution(tmp_path, monkeypatch, failed_write):
    snapshot = make_snapshot()
    callbacks = execution_callbacks()
    original_csv = cleanup.write_csv_report
    csv_calls = 0

    def fail_selected_csv(path, rows, **options):
        nonlocal csv_calls
        csv_calls += 1
        selected_call = {"backup": 1, "initial-csv": 2, "attempting-csv": 3}.get(failed_write)
        if csv_calls == selected_call:
            raise OSError("Synthetic disk failure")
        return original_csv(path, rows, **options)

    monkeypatch.setattr(cleanup, "write_csv_report", fail_selected_csv)
    if failed_write == "claim":
        callbacks["before_attempt"].side_effect = OSError("Synthetic DB claim failure")
    with pytest.raises(OSError):
        execute_fixture(snapshot, tmp_path, callbacks)
    callbacks["cancel_fn"].assert_not_called()


def test_mid_execution_csv_failure_preserves_known_submission_and_stops(tmp_path, monkeypatch):
    snapshot = make_snapshot(rows=[{"invitation_id": identifier, "last_modified": "2020/01/01"}
                                   for identifier in ("first", "second")])
    callbacks = execution_callbacks()
    original_csv = cleanup.write_csv_report

    def fail_submitted_csv(path, rows, **options):
        if not options.get("immutable") and any(row.get("status") == "submitted" for row in rows):
            raise OSError("Synthetic derived CSV failure")
        return original_csv(path, rows, **options)

    monkeypatch.setattr(cleanup, "write_csv_report", fail_submitted_csv)
    with pytest.raises(OSError):
        execute_fixture(snapshot, tmp_path, callbacks)
    callbacks["cancel_fn"].assert_called_once()
    paths = cleanup.artifact_paths(tmp_path, snapshot["batch_id"])
    persisted = cleanup.read_execution_results(paths["results_json"], snapshot, cleanup.payload_digest(snapshot))
    assert [item["status"] for item in persisted] == ["submitted", "not_processed"]


@pytest.mark.parametrize("mutation", ["incomplete", "empty", "hash", "wrong-store"])
def test_invalid_execution_evidence_never_calls_cancel(tmp_path, mutation):
    snapshot = make_snapshot(complete=mutation != "incomplete", rows=[] if mutation == "empty" else None)
    callbacks = execution_callbacks()
    overrides = {}
    if mutation == "hash":
        overrides["snapshot_sha256"] = "0" * 64
    if mutation == "wrong-store":
        callbacks["validate_identity"].side_effect = cleanup.TargetCleanupError("store-changed")
    with pytest.raises(cleanup.TargetCleanupError):
        execute_fixture(snapshot, tmp_path, callbacks, **overrides)
    callbacks["cancel_fn"].assert_not_called()


@pytest.mark.parametrize("flags", [[], ["--execute"], ["--yes"]])
def test_cli_gate_precedes_database_and_platform_access(monkeypatch, flags):
    sys.path.insert(0, str(PROJECT_ROOT))
    from scripts import cleanup_target_plans as entry

    forbidden = Mock(side_effect=AssertionError("Gate must run before database setup"))
    monkeypatch.setattr("assistant.database.engine.create_database_engine", forbidden)
    arguments = ["--mode", "execute", "--batch-id", str(uuid.uuid4()), "--job-id", str(uuid.uuid4()), *flags]
    with pytest.raises(SystemExit) as error:
        entry.main(arguments)
    assert error.value.code == 2
    forbidden.assert_not_called()


@pytest.mark.parametrize("lose_write_response", [False, True])
def test_script_runs_native_batch_claims_with_only_synthetic_platform(tmp_path, monkeypatch, lose_write_response):
    sys.path.insert(0, str(PROJECT_ROOT))
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from assistant.database.engine import create_database_engine
    from assistant.database.models import Base, Job, TargetCleanupBatch, TargetCleanupItem
    from assistant.services import target_cleanup_service as service
    from lib import target_invitation_dom as invitations
    from lib import target_invitation_navigation as navigation
    from scripts import cleanup_target_plans as entry

    engine = create_database_engine(tmp_path / "synthetic.sqlite3")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(service, "ensure_user_dirs", lambda: tmp_path)
    monkeypatch.setattr(service, "resolve_running_identity", lambda: {
        "store_id": "store-1", "store_name": "Synthetic store", "shop_id": "shop-1", "shop_region": "US",
    })
    monkeypatch.setattr(navigation, "navigate_from_seller_home_to_ongoing", lambda store_id: {
        "destination": {"shop_id": "shop-1", "shop_region": "US"},
    })
    monkeypatch.setattr(invitations, "scan_older_invitations", lambda store_id, **options: {
        "rows": [{"invitation_id": identifier, "last_modified": "2020/01/01"} for identifier in ("first", "second")],
        "scan_complete": True, "stop_reason": "first-page", "pages_scanned": 1,
    })
    monkeypatch.setattr("lib.zclaw.zclaw_invoke", Mock(side_effect=AssertionError("No real transport allowed")))
    cancel = Mock(side_effect=AssertionError("Preview must never cancel"))
    monkeypatch.setattr(invitations, "cancel_invitation_by_id", cancel)
    monkeypatch.setattr(invitations, "locate_target_invitation", Mock(return_value={"revalidated": True}))
    monkeypatch.setattr(cleanup.time, "sleep", Mock())

    def mark_job(job_id, status):
        with session_factory() as session:
            session.get(Job, job_id).status = status
            session.commit()

    try:
        preview = service.create_preview(session_factory, months=4, idempotency_key="synthetic-preview")
        mark_job(preview["job_id"], "running")
        assert entry.run_batch(session_factory, batch_id=preview["batch_id"], job_id=preview["job_id"], mode="preview") == 0
        cancel.assert_not_called()
        mark_job(preview["job_id"], "succeeded")
        paths = cleanup.artifact_paths(tmp_path / "exports" / "target_cleanup", preview["batch_id"])
        candidate_bytes = paths["candidates_csv"].read_bytes()
        execution = service.create_execution(session_factory, preview["batch_id"], "y", "synthetic-execute")
        mark_job(execution["job_id"], "running")
        claimed_request = service.begin_script(session_factory, execution["batch_id"], execution["job_id"], "execute")
        with pytest.raises(service.TargetCleanupServiceError):
            entry.run_batch(session_factory, batch_id=execution["batch_id"], job_id=execution["job_id"],
                            mode="execute", execute=True, yes=True)
        with session_factory() as session:
            assert session.get(TargetCleanupBatch, execution["batch_id"]).execute_status == "running"
        cancel.side_effect = TimeoutError("Synthetic response lost") if lose_write_response else None
        cancel.return_value = {"status": "submitted", "action": "cancel:synthetic", "reason": "operation-submitted"}
        return_code = entry._run_claimed_batch(session_factory, claimed_request, execute=True, yes=True)
        assert return_code == (1 if lose_write_response else 0)
        assert cancel.call_count == (1 if lose_write_response else 2)
        assert paths["candidates_csv"].read_bytes() == candidate_bytes
        with session_factory() as session:
            batch = session.get(TargetCleanupBatch, preview["batch_id"])
            assert batch.execute_status == ("needs_review" if lose_write_response else "completed")
            statuses = list(session.scalars(select(TargetCleanupItem.status).order_by(TargetCleanupItem.position)))
            assert statuses == (["uncertain", "not_processed"] if lose_write_response else ["submitted", "submitted"])
        with pytest.raises(service.TargetCleanupServiceError):
            entry.run_batch(session_factory, batch_id=execution["batch_id"], job_id=execution["job_id"],
                            mode="execute", execute=True, yes=True)
        assert cancel.call_count == (1 if lose_write_response else 2)
    finally:
        engine.dispose()
