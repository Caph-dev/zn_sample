"""Offline D+15 job admission and fixed-process/report contracts."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.api import jobs as jobs_api
from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import Base, FollowupTask, Job, SampleCase, Store
from assistant.jobs.handlers import unfulfilled
from assistant.jobs.progress import list_events, update_progress
from assistant.jobs.registry import HandlerFailure
from assistant.jobs.worker import worker_loop_once
from assistant.services.release_lifecycle import LifecycleCoordinator, install_release_guard


@pytest.fixture
def job_environment(tmp_path, monkeypatch):
    engine = create_database_engine(tmp_path / "assistant.sqlite3")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session_factory.release_coordinator = LifecycleCoordinator()
    candidates = []
    with session_factory() as session:
        for store_id, ziniao_store_id in ((1, "store-one"), (2, "store-two")):
            session.add(Store(id=store_id, ziniao_store_id=ziniao_store_id, store_name=ziniao_store_id))
        session.flush()
        for task_id, store_id, scheduled_for in (
            (1, 1, date(2026, 10, 3)), (2, 1, date(2026, 10, 1)),
            (3, 2, date(2026, 10, 2)), (4, 1, date(2026, 10, 1)),
        ):
            session.add(SampleCase(id=task_id, store_id=store_id, creator_id=str(task_id),
                                   creator_name=f"creator-{task_id}", apply_id=str(task_id), product_id="hero"))
            session.flush()
            session.add(FollowupTask(id=task_id, sample_case_id=task_id, stage="unfulfilled", scheduled_for=scheduled_for))
            candidates.append({"task_id": task_id, "store_id": "store-one" if store_id == 1 else "store-two"})
        session.commit()
    service = Mock()
    service.list_unfulfilled_candidates.return_value = candidates
    monkeypatch.setattr(jobs_api, "FollowupService", Mock(return_value=service))
    monkeypatch.setattr(unfulfilled, "ensure_user_dirs", lambda: tmp_path)
    monkeypatch.setattr(unfulfilled, "user_exports_dir", lambda: tmp_path / "exports")
    monkeypatch.setattr(unfulfilled, "application_resource_dir", lambda: PROJECT_ROOT)
    monkeypatch.setattr(unfulfilled, "assert_private_interpreter", lambda: None)
    monkeypatch.setattr(unfulfilled, "python_subprocess_environment", lambda: {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    application = create_app(port=8765)
    application.state.session_factory = session_factory
    install_release_guard(application, session_factory.release_coordinator)
    with TestClient(application, base_url="http://127.0.0.1:8765") as client:
        yield SimpleNamespace(session_factory=session_factory, client=client, service=service)
    engine.dispose()


def post_batch(environment, fields, *, query=""):
    return environment.client.post(
        "/api/jobs/followups/unfulfilled" + query,
        content=urlencode(fields),
        headers={"Origin": "http://127.0.0.1:8765", "Content-Type": "application/x-www-form-urlencoded"},
    )


def read_job(environment, job_id):
    with environment.session_factory() as session:
        return session.get(Job, job_id)


@pytest.mark.parametrize("fields", [
    [("task_ids", "1")],
    [("task_ids", "1"), ("confirmation", "yes")],
    [("confirmation", "y")],
    [("task_ids", "0"), ("confirmation", "y")],
    [("task_ids", "1.0"), ("confirmation", "y")],
    [("task_ids", "1"), ("confirmation", "y"), ("execute_limit", "0")],
    [("task_ids", "1"), ("confirmation", "y"), ("execute_limit", "-1")],
    [("task_ids", "1"), ("confirmation", "y"), ("execute_limit", "1.5")],
    [("task_ids", "1"), ("confirmation", "y"), ("execute_limit", "")],
    [("task_ids", "1"), ("confirmation", "y"), ("execute_limit", "true")],
    [("task_ids", "1"), ("confirmation", "y"), ("store_id", "store-one")],
    [("task_ids", "1"), ("confirmation", "y"), ("confirmation", "y")],
    [("task_ids", "1"), ("confirmation", "y"), ("execute_limit", "1"), ("execute_limit", "2")],
])
def test_invalid_inputs_create_no_job(job_environment, fields):
    assert post_batch(job_environment, fields).status_code == 400
    job_environment.service.list_unfulfilled_candidates.assert_not_called()
    with job_environment.session_factory() as session:
        assert session.scalar(select(Job.id)) is None


def test_query_json_and_file_inputs_create_no_job(job_environment):
    fields = [("task_ids", "1"), ("confirmation", "y")]
    assert post_batch(job_environment, fields, query="?execute_limit=2").status_code == 400
    headers = {"Origin": "http://127.0.0.1:8765"}
    assert job_environment.client.post("/api/jobs/followups/unfulfilled", json=dict(fields), headers=headers).status_code == 415
    response = job_environment.client.post("/api/jobs/followups/unfulfilled", headers=headers,
                                           files={"task_ids": ("tasks.txt", b"1")}, data={"confirmation": "y"})
    assert response.status_code == 400
    with job_environment.session_factory() as session:
        assert session.scalar(select(Job.id)) is None


def test_candidate_and_same_store_validation_precede_creation(job_environment):
    for task_ids, expected_status in (([999], 409), ([1, 3], 400)):
        fields = [("task_ids", str(task_id)) for task_id in task_ids] + [("confirmation", "y")]
        assert post_batch(job_environment, fields).status_code == expected_status
    job_environment.service.list_unfulfilled_candidates.return_value = []
    assert post_batch(job_environment, [("task_ids", "1"), ("confirmation", "y")]).status_code == 409
    with job_environment.session_factory() as session:
        assert session.scalar(select(Job.id)) is None


def test_default_limit_and_stable_freeze_use_server_schedule(job_environment):
    response = post_batch(job_environment, [("task_ids", "1"), ("task_ids", "4"),
                                            ("task_ids", "2"), ("confirmation", " Y ")])
    assert response.status_code == 200
    payload = response.json()
    assert payload["task_ids"] == [2]
    assert payload["count"] == payload["execute_limit"] == 1
    job = read_job(job_environment, payload["job_id"])
    assert job.job_type == "followup_unfulfilled_write"
    assert job.store_id == "store-one"
    assert json.loads(job.result_summary) == {"task_ids": [2], "store_id": "store-one", "execute_limit": 1}
    call = job_environment.service.list_unfulfilled_candidates.call_args
    assert call.args == (None,)
    assert isinstance(call.kwargs["today"], date)


@pytest.mark.parametrize("status", ["pending", "running"])
def test_deduplication_returns_original_scope_without_overwriting(job_environment, status):
    first = post_batch(job_environment, [("task_ids", "1"), ("task_ids", "2"),
                                        ("execute_limit", "2"), ("confirmation", "y")]).json()
    with job_environment.session_factory() as session:
        session.get(Job, first["job_id"]).status = status
        session.commit()
    saved_summary = read_job(job_environment, first["job_id"]).result_summary
    second = post_batch(job_environment, [("task_ids", "4"), ("execute_limit", "5"), ("confirmation", "y")]).json()
    assert second == {**first, "deduplicated": True}
    assert second["task_ids"] == [2, 1]
    assert second["count"] == 2
    assert read_job(job_environment, first["job_id"]).result_summary == saved_summary


def test_running_scope_is_reused_after_first_row_leaves_candidates(job_environment):
    first = post_batch(job_environment, [("task_ids", "1"), ("task_ids", "2"),
                                        ("execute_limit", "2"), ("confirmation", "y")]).json()
    report = {
        "task_ids": [2, 1], "store_id": "store-one", "execute_limit": 2,
        "mode": unfulfilled.JOB_TYPE, "rows": [{"task_id": 2, "result": "written"}],
        "counts": {"written": 1}, "json_path": "checkpoint.json", "csv_path": "checkpoint.csv",
        "backup_path": "backup.json", "stopped_reason": "",
    }
    with job_environment.session_factory() as session:
        job = session.get(Job, first["job_id"])
        job.status = "running"
        job.result_summary = json.dumps(report)
        session.get(FollowupTask, 2).send_result = "unfulfilled-written"
        session.commit()
    job_environment.service.list_unfulfilled_candidates.reset_mock()
    job_environment.service.list_unfulfilled_candidates.return_value = []
    response = post_batch(job_environment, [("task_ids", "2"), ("task_ids", "1"),
                                           ("execute_limit", "1"), ("confirmation", "y")])
    assert response.status_code == 200
    assert response.json() == {**first, "deduplicated": True}
    assert json.loads(read_job(job_environment, first["job_id"]).result_summary) == report
    # Dedup does not waive ownership: mixed local stores are always rejected.
    assert post_batch(job_environment, [("task_ids", "2"), ("task_ids", "3"),
                                        ("confirmation", "y")]).status_code == 400
    job_environment.service.list_unfulfilled_candidates.assert_not_called()
    with job_environment.session_factory() as session:
        session.get(Job, first["job_id"]).status = "succeeded"
        session.commit()
    assert post_batch(job_environment, [("task_ids", "2"), ("confirmation", "y")]).status_code == 409
    assert job_environment.service.list_unfulfilled_candidates.call_count == 1


def test_synchronous_admission_runs_outside_async_event_loop(job_environment):
    def list_candidates(*arguments, **options):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return [{"task_id": 1, "store_id": "store-one"}]

    job_environment.service.list_unfulfilled_candidates.side_effect = list_candidates
    assert post_batch(job_environment, [("task_ids", "1"), ("confirmation", "y")]).status_code == 200


def test_admission_stopping_rejects_before_candidate_read(job_environment):
    job_environment.session_factory.release_coordinator.stopping = True
    response = post_batch(job_environment, [("task_ids", "1"), ("confirmation", "y")])
    assert response.status_code == 503
    job_environment.service.list_unfulfilled_candidates.assert_not_called()
    with job_environment.session_factory() as session:
        assert session.scalar(select(Job.id)) is None


def enqueue_batch(environment):
    response = post_batch(environment, [("task_ids", "1"), ("task_ids", "2"),
                                        ("execute_limit", "2"), ("confirmation", "y")])
    assert response.status_code == 200
    return response.json()["job_id"]


def install_fake_process(monkeypatch, job_id, *, return_code=0, results=None, report_override=None,
                         missing_report=False, on_exit=None):
    request_payload = {"task_ids": [2, 1], "store_id": "store-one", "execute_limit": 2}
    result_names = results or ["written", "waiting-business-update"]
    captured_calls = []

    class FakeProcess:
        def __init__(self, argv, **options):
            captured_calls.append((argv, options))
            self.poll_count = 0
            report_path = unfulfilled.unfulfilled_report_path(job_id)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report = {
                **request_payload, "json_path": str(report_path),
                "mode": unfulfilled.JOB_TYPE, "stopped_reason": "",
                "run_id": job_id, "selected_at": "2026-10-08T12:00:00+08:00", "execute": True,
                "csv_path": str(report_path.with_suffix(".csv")),
                "backup_path": str(report_path.with_name(f"{job_id}_pre_execute.json")),
                "rows": [{"task_id": task_id, "result": result_name}
                         for task_id, result_name in zip(request_payload["task_ids"], result_names)],
                "counts": {result_name: result_names.count(result_name) for result_name in result_names},
                **(report_override or {}),
            }
            if not missing_report:
                report_path.write_text(json.dumps(report), encoding="utf-8")

        def poll(self):
            self.poll_count += 1
            return None if self.poll_count == 1 else return_code

        def wait(self):
            if on_exit is not None:
                on_exit()
            return return_code

        def terminate(self):
            pytest.fail("D+15 writing process must never be terminated")

        def kill(self):
            pytest.fail("D+15 writing process must never be killed")

    monkeypatch.setattr(unfulfilled.subprocess, "Popen", FakeProcess)
    return captured_calls


def test_worker_fixed_argv_and_success_report_contract(job_environment, monkeypatch):
    job_id = enqueue_batch(job_environment)

    def publish_script_progress():
        update_progress(job_environment.session_factory, job_id, current=2, total=2, message="Script row progress")

    captured_calls = install_fake_process(monkeypatch, job_id, on_exit=publish_script_progress)
    assert worker_loop_once(job_environment.session_factory) == job_id
    argv, options = captured_calls[0]
    assert argv == [sys.executable, str(PROJECT_ROOT / "scripts" / "mark_unfulfilled_followups.py"),
                    "--job-id", job_id, "--execute", "--yes", "--write-feishu", "--execute-limit", "2"]
    assert options["shell"] is False
    assert options["env"]["PYTHONUTF8"] == "1"
    assert options["env"]["PYTHONIOENCODING"] == "utf-8"
    job = read_job(job_environment, job_id)
    assert job.status == "succeeded"
    summary = json.loads(job.result_summary)
    assert summary["task_ids"] == [2, 1]
    assert summary["counts"] == {"written": 1, "waiting-business-update": 1}
    assert summary["json_path"] == str(unfulfilled.unfulfilled_report_path(job_id))
    assert summary["json_path"].endswith(f"{job_id}_result.json")
    assert summary["csv_path"].endswith(f"{job_id}_result.csv")
    assert summary["backup_path"].endswith(f"{job_id}_pre_execute.json")
    assert set(summary) == unfulfilled.SUMMARY_KEYS
    assert job.progress_current == job.progress_total == 2
    progress_events = [event for event in list_events(job_environment.session_factory, job_id)
                       if event["event_type"] == "job.progress"]
    assert [event["message"] for event in progress_events] == ["Script row progress"]
    assert Path(job.log_path).exists()


def test_worker_failed_unknown_report_keeps_successes_and_scope(job_environment, monkeypatch):
    job_id = enqueue_batch(job_environment)
    install_fake_process(monkeypatch, job_id, return_code=1, results=["written", "write-unknown"])
    assert worker_loop_once(job_environment.session_factory) == job_id
    job = read_job(job_environment, job_id)
    assert job.status == "failed"
    assert job.error_code == "unfulfilled-write-failed-1"
    summary = json.loads(job.result_summary)
    assert summary["task_ids"] == [2, 1]
    assert summary["execute_limit"] == 2
    assert summary["rows"][0]["result"] == "written"
    assert summary["rows"][1]["result"] == "write-unknown"
    assert Path(summary["json_path"]).exists()


@pytest.mark.parametrize("stopped_reason,missing_report", [
    ("artifact-failed-before-write", True),
    ("artifact-failed-after-row", False),
])
def test_latest_artifact_failure_db_results_survive_stale_file_and_worker_failure(
    job_environment, monkeypatch, stopped_reason, missing_report,
):
    job_id = enqueue_batch(job_environment)
    latest_summary = {
        "task_ids": [2, 1], "store_id": "store-one", "execute_limit": 2,
        "mode": unfulfilled.JOB_TYPE,
        "rows": [{"task_id": 2, "result": "written" if not missing_report else "not-processed"},
                 {"task_id": 1, "result": "not-processed"}],
        "counts": {"written": 1, "not-processed": 1} if not missing_report else {"not-processed": 2},
        "json_path": "", "csv_path": "", "backup_path": "known_backup.json",
        "stopped_reason": stopped_reason,
    }

    def publish_script_failure():
        with job_environment.session_factory() as session:
            session.get(Job, job_id).result_summary = json.dumps(latest_summary)
            session.commit()

    install_fake_process(monkeypatch, job_id, return_code=1, results=["not-processed", "not-processed"],
                         missing_report=missing_report, on_exit=publish_script_failure)
    worker_loop_once(job_environment.session_factory)
    job = read_job(job_environment, job_id)
    assert job.status == "failed"
    assert job.error_code == "unfulfilled-write-failed-1"
    assert json.loads(job.result_summary) == latest_summary
    if not missing_report:
        file_checkpoint = json.loads(unfulfilled.unfulfilled_report_path(job_id).read_text())
        assert file_checkpoint["rows"][0]["result"] == "not-processed"
        assert file_checkpoint["json_path"] != ""


def test_mismatching_db_artifact_failure_does_not_replace_matching_report(job_environment, monkeypatch):
    job_id = enqueue_batch(job_environment)

    def publish_mismatching_failure():
        with job_environment.session_factory() as session:
            session.get(Job, job_id).result_summary = json.dumps({
                "task_ids": [999], "store_id": "store-one", "execute_limit": 2,
                "mode": unfulfilled.JOB_TYPE, "rows": [], "counts": {},
                "json_path": "", "csv_path": "", "backup_path": "", "stopped_reason": "artifact-failed-after-row",
            })
            session.commit()

    install_fake_process(monkeypatch, job_id, on_exit=publish_mismatching_failure)
    worker_loop_once(job_environment.session_factory)
    summary = json.loads(read_job(job_environment, job_id).result_summary)
    assert summary["task_ids"] == [2, 1]
    assert summary["json_path"] == str(unfulfilled.unfulfilled_report_path(job_id))


def test_report_path_matches_parent_script(job_environment, monkeypatch):
    from scripts import mark_unfulfilled_followups as script

    monkeypatch.setattr(script, "user_exports_dir", unfulfilled.user_exports_dir)
    paths = script.report_paths("job-contract")
    assert unfulfilled.unfulfilled_report_path("job-contract") == paths["json_path"]
    assert paths["csv_path"].name == "job-contract_result.csv"
    assert paths["backup_path"].name == "job-contract_pre_execute.json"


@pytest.mark.parametrize("artifact_failure", [False, True])
def test_actual_parent_batch_report_is_consumed_without_losing_latest_rows(
    job_environment, monkeypatch, artifact_failure,
):
    from scripts import mark_unfulfilled_followups as script

    job_id = enqueue_batch(job_environment)
    monkeypatch.setattr(script, "user_exports_dir", unfulfilled.user_exports_dir)
    service = job_environment.service
    service.mark_unfulfilled.return_value = {"result": "written", "reason": ""}
    monkeypatch.setattr(script, "FollowupService", lambda factory: service)
    save_report = script.save_report
    save_count = 0

    def save_or_fail(summary, factory, *, job_id):
        nonlocal save_count
        save_count += 1
        if artifact_failure and save_count == 2:
            raise OSError("Synthetic filesystem failure after first write")
        save_report(summary, factory, job_id=job_id)

    monkeypatch.setattr(script, "save_report", save_or_fail)

    class LocalScriptProcess:
        def __init__(self, argv, **options):
            assert argv[argv.index("--job-id") + 1] == job_id

        def wait(self):
            request_payload = script.load_job_request(job_environment.session_factory, job_id, execute_limit=2)
            exit_code, _summary = script.run_batch(
                job_environment.session_factory, **request_payload, execute=True, job_id=job_id,
            )
            return exit_code

    monkeypatch.setattr(unfulfilled.subprocess, "Popen", LocalScriptProcess)
    worker_loop_once(job_environment.session_factory)
    job = read_job(job_environment, job_id)
    summary = json.loads(job.result_summary)
    assert set(summary) == unfulfilled.SUMMARY_KEYS
    assert summary["task_ids"] == [2, 1]
    assert summary["rows"][0]["result"] == "written"
    assert Path(summary["backup_path"]).exists()
    progress_events = [event for event in list_events(job_environment.session_factory, job_id)
                       if event["event_type"] == "job.progress"]
    if artifact_failure:
        assert job.status == "failed"
        assert summary["counts"] == {"written": 1, "not-processed": 1}
        assert summary["stopped_reason"] == "artifact-failed-after-row"
        assert summary["json_path"] == summary["csv_path"] == ""
        assert service.mark_unfulfilled.call_count == 1
        assert not progress_events
        checkpoint = json.loads(unfulfilled.unfulfilled_report_path(job_id).read_text())
        assert checkpoint["rows"][0]["result"] == "not-processed"
    else:
        assert job.status == "succeeded"
        assert summary["counts"] == {"written": 2}
        assert summary["stopped_reason"] == ""
        assert Path(summary["json_path"]).exists()
        assert Path(summary["csv_path"]).exists()
        assert len(progress_events) == 2


@pytest.mark.parametrize("return_code,report_override,missing_report,error_code", [
    (0, {}, True, "unfulfilled-report-missing"),
    (2, {}, True, "unfulfilled-write-failed-2"),
    (0, {"task_ids": [999]}, False, "unfulfilled-report-invalid"),
])
def test_absent_or_mismatched_report_never_erases_frozen_request(
    job_environment, monkeypatch, return_code, report_override, missing_report, error_code,
):
    job_id = enqueue_batch(job_environment)
    original_summary = read_job(job_environment, job_id).result_summary
    install_fake_process(monkeypatch, job_id, return_code=return_code,
                         report_override=report_override, missing_report=missing_report)
    worker_loop_once(job_environment.session_factory)
    job = read_job(job_environment, job_id)
    assert job.status == "failed"
    assert job.error_code == error_code
    assert job.result_summary == original_summary


def test_handler_rejects_unbound_request_before_process_creation(job_environment, monkeypatch):
    job_id = enqueue_batch(job_environment)
    with job_environment.session_factory() as session:
        job = session.get(Job, job_id)
        job.status = "running"
        job.store_id = "different-store"
        session.commit()
    process_factory = Mock(side_effect=AssertionError("Unexpected subprocess"))
    monkeypatch.setattr(unfulfilled.subprocess, "Popen", process_factory)
    with pytest.raises(HandlerFailure, match="scope or limit"):
        unfulfilled.run_unfulfilled_write(job_id, job_environment.session_factory)
    process_factory.assert_not_called()
