"""Temporary-SQLite D+15 gates and write/checkpoint boundaries; no remote calls."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from itertools import product
from urllib.parse import urlencode
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, ContentEvidence, FollowupTask, Job, SampleCase, Shipment, Store
from assistant.services.followup_service import FollowupService
from scripts import mark_unfulfilled_followups as batch

NOW = datetime(2026, 10, 8, 4, tzinfo=timezone.utc)


@pytest.fixture
def local_database(tmp_path, monkeypatch):
    engine = create_database_engine(tmp_path / "test.sqlite3")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(batch, "user_exports_dir", lambda: tmp_path / "exports")
    monkeypatch.setattr(batch, "beijing_now", lambda: NOW)
    monkeypatch.setattr("assistant.services.followup_service.beijing_now", lambda *arguments: NOW)
    yield factory
    engine.dispose()


def add_task(factory, *, days=16, store_id="store-a", **task_values):
    with factory() as session:
        store = session.scalar(select(Store).where(Store.ziniao_store_id == store_id))
        if store is None:
            store = Store(ziniao_store_id=store_id, store_name=store_id)
            session.add(store)
            session.flush()
        sample_case = SampleCase(
            store_id=store.id, creator_id=f"creator-{days}-{store_id}-{len(session.scalars(select(SampleCase)).all())}",
            creator_name="creator", apply_id=str(len(session.scalars(select(SampleCase)).all())),
            product_id="other-hero-product", sample_product_option="B006",
            platform_status="processing", platform_status_stale=False,
            feishu_cooperation_status="待发布",
        )
        session.add(sample_case)
        session.flush()
        session.add(Shipment(sample_case_id=sample_case.id, delivered_at=NOW - timedelta(days=days), status_category="delivered"))
        task = FollowupTask(sample_case_id=sample_case.id, stage="unfulfilled", scheduled_for=(NOW - timedelta(days=days - 15)).date(), action_kind="mark_unfulfilled", **task_values)
        session.add(task)
        session.commit()
        return task.id


@pytest.mark.parametrize("switches", [combination for combination in product((False, True), repeat=3) if any(combination) and not all(combination)])
def test_partial_write_gates_exit_before_loading_config_or_database(switches):
    arguments = batch.build_parser().parse_args(["--store-id", "store-a"])
    arguments.execute, arguments.yes, arguments.write_feishu = switches
    with patch.object(batch, "load_dotenv") as configuration, patch.object(batch, "open_existing_database") as database:
        assert batch.run(arguments) == 2
    configuration.assert_not_called()
    database.assert_not_called()


@pytest.mark.parametrize("extra_arguments", [["--execute-limit", "0"], ["--execute-limit", "-2"], ["--task-id", "0"], ["--store-id", ""]])
def test_invalid_local_inputs_never_initialize_database(extra_arguments):
    arguments = batch.build_parser().parse_args(["--store-id", "store-a", *extra_arguments])
    with patch.object(batch, "open_existing_database") as database:
        assert batch.run(arguments) == 2
    database.assert_not_called()


def test_preview_is_local_store_isolated_and_uses_current_calendar(local_database):
    add_task(local_database, days=14)
    day_15 = add_task(local_database, days=15)
    day_16 = add_task(local_database, days=16)
    add_task(local_database, store_id="other-store")
    add_task(local_database, send_result="unfulfilled-writing")
    with patch.object(FollowupService, "_write_cooperation_status") as remote:
        exit_code, summary = batch.run_batch(local_database, store_id="store-a", execute=False)
    assert exit_code == 0
    assert summary["task_ids"] == [day_16, day_15]
    assert summary["counts"] == {"planned": 2}
    assert summary["backup_path"] == ""
    assert len(json.loads(batch.Path(summary["json_path"]).read_text())["rows"]) == 2
    remote.assert_not_called()


def test_confirmed_content_excludes_candidate_and_missing_record_is_not_a_gate(local_database):
    excluded_id = add_task(local_database)
    allowed_id = add_task(local_database)
    with local_database() as session:
        task = session.get(FollowupTask, excluded_id)
        session.add(ContentEvidence(sample_case_id=task.sample_case_id, content_type="video", content_status="confirmed"))
        session.commit()
    exit_code, summary = batch.run_batch(local_database, store_id="store-a", execute=False)
    assert exit_code == 0
    assert summary["task_ids"] == [allowed_id]
    assert summary["rows"][0]["record_id"] == ""


@pytest.mark.parametrize("limit,expected_writes", [(1, 1), (2, 2)])
def test_explicit_limit_consumed_by_attempt_not_by_success(local_database, limit, expected_writes):
    task_ids = [add_task(local_database) for _ in range(3)]
    with patch.object(FollowupService, "_write_cooperation_status", return_value={"status": "blocked-unexpected-status", "current_status": "待发货"}) as remote:
        exit_code, summary = batch.run_batch(local_database, store_id="store-a", execute=True, execute_limit=limit)
    assert exit_code == 0
    assert remote.call_count == expected_writes
    assert summary["task_ids"] == task_ids[:limit]
    assert summary["counts"] == {"waiting-business-update": limit}
    with local_database() as session:
        for task_id in task_ids:
            task = session.get(FollowupTask, task_id)
            assert task.status == "pending"
            assert not task.send_result
    assert batch.Path(summary["backup_path"]).is_file()


def test_backup_failure_performs_zero_writes(local_database):
    add_task(local_database)
    with patch.object(batch, "write_pre_execute_backup", side_effect=OSError("disk full")), patch.object(FollowupService, "_write_cooperation_status") as remote:
        exit_code, summary = batch.run_batch(local_database, store_id="store-a", execute=True)
    assert exit_code == 1
    assert summary["stopped_reason"] == "artifact-failed-before-write"
    remote.assert_not_called()


def test_success_then_unknown_preserves_completed_row_and_stops_rest(local_database):
    task_ids = [add_task(local_database) for _ in range(3)]
    with patch.object(FollowupService, "_write_cooperation_status", side_effect=[{"status": "written"}, {"status": "write-uncertain", "error": "lost reply"}]) as remote:
        exit_code, summary = batch.run_batch(local_database, store_id="store-a", execute=True, execute_limit=3)
    assert exit_code == 1
    assert remote.call_count == 2
    assert [row["result"] for row in summary["rows"]] == ["written", "write-unknown", "not-processed"]
    assert json.loads(batch.Path(summary["json_path"]).read_text())["stopped_reason"] == "write-unknown"
    with local_database() as session:
        assert session.get(FollowupTask, task_ids[0]).send_result == "unfulfilled-written"
        assert session.get(FollowupTask, task_ids[1]).send_result == "unfulfilled-write-unknown"
        assert session.get(FollowupTask, task_ids[2]).send_result == ""


def test_result_persistence_failure_stops_before_next_row(local_database):
    task_ids = [add_task(local_database) for _ in range(2)]
    original_save = batch.save_report
    calls = 0

    def save_until_first_result(summary, factory, **keywords):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full after successful write")
        return original_save(summary, factory, **keywords)

    with patch.object(batch, "save_report", side_effect=save_until_first_result), patch.object(FollowupService, "_write_cooperation_status", return_value={"status": "written"}) as remote:
        exit_code, summary = batch.run_batch(local_database, store_id="store-a", execute=True, execute_limit=2)
    assert exit_code == 1
    assert remote.call_count == 1
    assert summary["stopped_reason"] == "artifact-failed-after-row"
    with local_database() as session:
        assert session.get(FollowupTask, task_ids[0]).send_result == "unfulfilled-written"
        assert session.get(FollowupTask, task_ids[1]).send_result == ""


def test_internal_job_reads_only_frozen_scope_and_rejects_replay(local_database):
    chosen_id = add_task(local_database)
    add_task(local_database)
    job_id = "fixed-server-job"
    payload = {"task_ids": [chosen_id], "store_id": "store-a", "execute_limit": 1}
    with local_database() as session:
        session.add(Job(id=job_id, job_type=batch.JOB_TYPE, store_id="store-a", status="running", result_summary=json.dumps(payload)))
        session.commit()
    arguments = batch.build_parser().parse_args(["--job-id", job_id, "--execute", "--yes", "--write-feishu"])
    with patch.object(batch, "load_dotenv"), patch.object(FollowupService, "_write_cooperation_status", return_value={"status": "unchanged"}) as remote:
        assert batch.run(arguments, session_factory=local_database) == 0
        assert batch.run(arguments, session_factory=local_database) == 2
    assert remote.call_count == 1
    with local_database() as session:
        summary = json.loads(session.get(Job, job_id).result_summary)
        assert summary["task_ids"] == [chosen_id]
        assert summary["counts"] == {"unchanged": 1}


def test_disk_failure_retains_latest_rows_in_job_instead_of_advertising_stale_report(local_database):
    task_ids = [add_task(local_database) for _ in range(2)]
    job_id = "artifact-failure-job"
    payload = {"task_ids": task_ids, "store_id": "store-a", "execute_limit": 2}
    with local_database() as session:
        session.add(Job(id=job_id, job_type=batch.JOB_TYPE, store_id="store-a", status="running", result_summary=json.dumps(payload)))
        session.commit()
    original_save = batch.save_report
    calls = 0

    def fail_after_remote_write(summary, factory, **keywords):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("result disk unavailable")
        original_save(summary, factory, **keywords)

    with patch.object(batch, "save_report", side_effect=fail_after_remote_write), patch.object(FollowupService, "_write_cooperation_status", return_value={"status": "written"}) as remote:
        exit_code, _summary = batch.run_batch(
            local_database, store_id="store-a", execute=True,
            execute_limit=2, task_ids=task_ids, job_id=job_id,
        )
    assert exit_code == 1
    assert remote.call_count == 1
    with local_database() as session:
        summary = json.loads(session.get(Job, job_id).result_summary)
    assert [row["result"] for row in summary["rows"]] == ["written", "not-processed"]
    assert summary["stopped_reason"] == "artifact-failed-after-row"
    assert summary["json_path"] == summary["csv_path"] == ""
    assert batch.Path(summary["backup_path"]).is_file()


def test_real_api_worker_script_and_service_preserve_partial_batch_results(local_database, tmp_path, monkeypatch):
    from assistant.api import jobs as jobs_api
    from assistant.app import create_app
    from assistant.jobs.handlers import unfulfilled
    from assistant.jobs.worker import worker_loop_once

    task_ids = [add_task(local_database) for _ in range(3)]
    monkeypatch.setattr(jobs_api, "datetime", type("FixedClock", (), {"now": staticmethod(lambda *arguments: NOW)}))
    monkeypatch.setattr(unfulfilled, "user_exports_dir", lambda: tmp_path / "exports")
    monkeypatch.setattr(unfulfilled, "ensure_user_dirs", lambda: tmp_path)
    monkeypatch.setattr(unfulfilled, "assert_private_interpreter", lambda: None)
    monkeypatch.setattr(batch, "load_dotenv", lambda: None)
    monkeypatch.setattr(batch, "configure_logging", lambda **keywords: None)
    fixed_commands = []

    class InProcessScript:
        def __init__(self, argv, **options):
            assert options["shell"] is False
            fixed_commands.append(argv)
            arguments = batch.build_parser().parse_args(argv[2:])
            self.return_code = batch.run(arguments, session_factory=local_database)

        def poll(self):
            return self.return_code

        def wait(self):
            return self.return_code

    monkeypatch.setattr(unfulfilled.subprocess, "Popen", InProcessScript)
    application = create_app(port=8765)
    application.state.session_factory = local_database
    with TestClient(application, base_url="http://127.0.0.1:8765") as client:
        fields = [("task_ids", str(task_id)) for task_id in reversed(task_ids)]
        fields.extend([("execute_limit", "3"), ("confirmation", "y")])
        response = client.post(
            "/api/jobs/followups/unfulfilled", content=urlencode(fields),
            headers={"Origin": "http://127.0.0.1:8765", "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        with patch.object(FollowupService, "_write_cooperation_status", side_effect=[
            {"status": "written"}, {"status": "write-uncertain", "error": "reply lost"},
        ]) as remote:
            assert worker_loop_once(local_database) == job_id
        assert remote.call_count == 2
        job_payload = client.get(f"/api/jobs/{job_id}").json()
    assert len(fixed_commands) == 1
    assert job_payload["status"] == "failed"
    assert job_payload["can_cancel"] is False
    summary = json.loads(job_payload["result_summary"])
    assert summary["task_ids"] == task_ids
    assert [row["result"] for row in summary["rows"]] == ["written", "write-unknown", "not-processed"]
    assert batch.Path(summary["json_path"]) == unfulfilled.unfulfilled_report_path(job_id)
    assert json.loads(batch.Path(summary["json_path"]).read_text())["counts"] == summary["counts"]
    assert batch.Path(summary["csv_path"]).is_file()
    assert batch.Path(summary["backup_path"]).is_file()
    with local_database() as session:
        assert session.get(FollowupTask, task_ids[0]).send_result == "unfulfilled-written"
        assert session.get(FollowupTask, task_ids[1]).send_result == "unfulfilled-write-unknown"
        assert session.get(FollowupTask, task_ids[2]).send_result == ""


def test_absent_or_old_database_is_not_created_or_migrated(tmp_path):
    path = tmp_path / "absent.sqlite3"
    with patch.object(batch, "database_path", return_value=path):
        with pytest.raises(ValueError, match="missing"):
            batch.open_existing_database()
    assert not path.exists()
    engine = create_database_engine(path)
    Store.__table__.create(engine)
    engine.dispose()
    with patch.object(batch, "database_path", return_value=path):
        with pytest.raises(ValueError, match="migration"):
            batch.open_existing_database()
    engine = create_database_engine(path)
    assert not batch.inspect(engine).has_table("followup_tasks")
    engine.dispose()
