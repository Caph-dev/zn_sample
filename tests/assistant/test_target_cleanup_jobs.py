"""Registered cleanup workers use fixed children and preserve domain evidence."""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.database.models import Job, TargetCleanupBatch
from assistant.jobs.handlers import target_cleanup as handler
from assistant.jobs.progress import list_events
from assistant.jobs.registry import (
    COOPERATIVE_CANCEL_JOB_TYPES, REGISTERED_JOB_TYPES, WRITE_JOB_TYPES, ZINIAO_JOB_TYPES,
    HandlerFailure, can_request_cancellation, get_handler,
)
from assistant.jobs.worker import worker_loop_once
from assistant.services import target_cleanup_service as service
from tests.assistant.test_target_cleanup_api import (
    BATCHES_URL, cleanup_client, cleanup_environment, finish_synthetic_preview,
    set_job_status, write_synthetic_results,
)


def create_execution(environment, *, identifiers=("invite-1", "invite-2")):
    preview = service.create_preview(environment.session_factory, 4, "preview")
    finish_synthetic_preview(environment, preview, identifiers=identifiers)
    return service.create_execution(environment.session_factory, preview["batch_id"], "y", "execution")


def fake_cleanup_child(environment, *, statuses=("submitted", "submitted"), return_code=0,
                       preview_identifiers=("invite-1", "invite-2"), complete=True):
    def run(arguments, **options):
        mode = arguments[3]
        created = {"batch_id": arguments[5], "job_id": arguments[7]}
        options["stdout"].write("Synthetic cleanup child; no platform operations.\n")
        if mode == "preview":
            finish_synthetic_preview(environment, created, identifiers=preview_identifiers,
                                     complete=complete, terminal_job=False)
        else:
            service.begin_script(environment.session_factory, created["batch_id"], created["job_id"], mode)
            write_synthetic_results(environment, created["batch_id"], statuses)
        return SimpleNamespace(returncode=return_code)
    return run


def test_only_two_registered_cleanup_types_and_cancel_semantics():
    for job_type in (service.PREVIEW_JOB_TYPE, service.EXECUTE_JOB_TYPE):
        assert job_type in REGISTERED_JOB_TYPES and job_type in ZINIAO_JOB_TYPES
        assert get_handler(job_type) is handler.run_target_cleanup_job
        assert job_type not in COOPERATIVE_CANCEL_JOB_TYPES
        assert can_request_cancellation(job_type, "pending")
        assert not can_request_cancellation(job_type, "running")
    assert service.EXECUTE_JOB_TYPE in WRITE_JOB_TYPES
    assert service.PREVIEW_JOB_TYPE not in WRITE_JOB_TYPES
    assert get_handler("target_cleanup_arbitrary_mode") is None


@pytest.mark.parametrize("mode", ("preview", "execute"))
@pytest.mark.parametrize("platform_name,expected_flags", (("darwin", 0), ("win32", 512)))
def test_fixed_argv_utf8_private_interpreter_and_devnull(cleanup_environment, monkeypatch, mode, platform_name, expected_flags):
    environment = cleanup_environment
    created = (service.create_preview(environment.session_factory, 2, "preview")
               if mode == "preview" else create_execution(environment))
    set_job_status(environment, created["job_id"], "running")
    with environment.session_factory() as session:
        session.get(Job, created["job_id"]).result_summary = json.dumps({
            "batch_id": str(uuid.uuid4()), "mode": "execute", "store_id": "wrong",
            "command": "evil shell command", "arguments": ["--months", "999"],
        })
        session.commit()
    monkeypatch.setattr(sys, "platform", platform_name)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 512, raising=False)
    interpreter_guard = Mock()
    monkeypatch.setattr(handler, "assert_private_interpreter", interpreter_guard)
    child = Mock(side_effect=fake_cleanup_child(environment))
    monkeypatch.setattr(subprocess, "run", child)
    summary = json.loads(get_handler(f"target_cleanup_{mode}")(created["job_id"], environment.session_factory))
    expected_arguments = [sys.executable, str(PROJECT_ROOT / "scripts" / "cleanup_target_plans.py"),
                          "--mode", mode, "--batch-id", created["batch_id"], "--job-id", created["job_id"]]
    if mode == "execute":
        expected_arguments += ["--execute", "--yes"]
        assert "submitted" in summary["message"] and "not platform-final" in summary["message"]
    interpreter_guard.assert_called_once_with()
    child.assert_called_once()
    assert child.call_args.args == (expected_arguments,)
    options = child.call_args.kwargs
    assert options["cwd"] == str(PROJECT_ROOT)
    assert options["shell"] is False and options["check"] is False
    assert options["stdin"] == subprocess.DEVNULL and options["stderr"] == subprocess.STDOUT
    assert options["creationflags"] == expected_flags
    assert options["env"]["PYTHONUTF8"] == "1" and options["env"]["PYTHONIOENCODING"] == "utf-8"
    assert options["stdout"].encoding == "utf-8"
    assert summary["batch_id"] == created["batch_id"]
    assert summary["batch_url"] == f"/plan-cleanup?batch_id={created['batch_id']}"
    with environment.session_factory() as session:
        log_path = Path(session.get(Job, created["job_id"]).log_path)
    assert log_path.parent == environment.root / "logs" and log_path.is_file()
    assert "Synthetic cleanup child" in log_path.read_text(encoding="utf-8")
    events = list_events(environment.session_factory, created["job_id"])
    assert events[0]["message"].startswith("Starting cleanup")
    assert "completed" in events[-1]["message"] if mode == "preview" else "submitted" in events[-1]["message"]


@pytest.mark.parametrize("mode", ("preview", "execute"))
def test_handler_busy_check_precedes_child_identity_and_ignores_own_job(cleanup_environment, monkeypatch, mode):
    environment = cleanup_environment
    created = (service.create_preview(environment.session_factory, 4, "preview")
               if mode == "preview" else create_execution(environment))
    set_job_status(environment, created["job_id"], "running")
    with environment.session_factory() as session:
        session.add(Job(job_type="operator_tracking", status="pending", store_id="other-store"))
        session.commit()
    environment.identity.reset_mock()
    child = Mock(side_effect=AssertionError("Busy handler must not spawn"))
    monkeypatch.setattr(subprocess, "run", child)
    with pytest.raises(HandlerFailure) as captured:
        handler.run_target_cleanup_job(created["job_id"], environment.session_factory)
    assert captured.value.error_code == "store-busy"
    assert created["batch_id"] in captured.value.summary
    child.assert_not_called()
    environment.identity.assert_not_called()
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload[f"{mode}_status"] == ("failed" if mode == "preview" else "partial")


@pytest.mark.parametrize("violation,expected_code", (("pending", "job-claim-required"),
                                                      ("store", "store-binding-mismatch")))
def test_running_claim_and_store_binding_are_required(cleanup_environment, monkeypatch, violation, expected_code):
    environment = cleanup_environment
    created = service.create_preview(environment.session_factory, 4, "preview")
    if violation == "store":
        set_job_status(environment, created["job_id"], "running")
        with environment.session_factory() as session:
            session.get(Job, created["job_id"]).store_id = "changed-store"
            session.commit()
    environment.identity.reset_mock()
    child = Mock()
    monkeypatch.setattr(subprocess, "run", child)
    with pytest.raises(HandlerFailure) as captured:
        handler.run_target_cleanup_job(created["job_id"], environment.session_factory)
    assert captured.value.error_code == expected_code
    child.assert_not_called()
    environment.identity.assert_not_called()


def test_duplicate_handler_does_not_close_the_existing_running_claim(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    created = create_execution(environment)
    set_job_status(environment, created["job_id"], "running")
    service.begin_script(environment.session_factory, created["batch_id"], created["job_id"], "execute")
    child = Mock(side_effect=AssertionError("Duplicate handler must not spawn"))
    monkeypatch.setattr(subprocess, "run", child)
    with pytest.raises(HandlerFailure) as captured:
        handler.run_target_cleanup_job(created["job_id"], environment.session_factory)
    assert captured.value.error_code == "batch-already-claimed"
    child.assert_not_called()
    with environment.session_factory() as session:
        assert session.get(TargetCleanupBatch, created["batch_id"]).execute_status == "running"
        assert session.get(Job, created["job_id"]).status == "running"
    assert service.batch_payload(environment.session_factory, created["batch_id"])["counts"]["pending"] == 2


def test_result_summary_cannot_forge_missing_domain_binding(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    job_id = str(uuid.uuid4())
    with environment.session_factory() as session:
        session.add(Job(id=job_id, job_type=service.EXECUTE_JOB_TYPE, status="running", store_id="store-1",
                        result_summary=json.dumps({"batch_id": str(uuid.uuid4()), "mode": "execute"})))
        session.commit()
    child = Mock()
    monkeypatch.setattr(subprocess, "run", child)
    with pytest.raises(HandlerFailure, match="exactly one domain batch"):
        handler.run_target_cleanup_job(job_id, environment.session_factory)
    child.assert_not_called()
    environment.identity.assert_not_called()


@pytest.mark.parametrize("mode", ("preview", "execute"))
def test_spawn_failure_syncs_domain_without_retry_or_sensitive_exception(cleanup_environment, monkeypatch, mode):
    environment = cleanup_environment
    created = (service.create_preview(environment.session_factory, 4, "preview")
               if mode == "preview" else create_execution(environment))
    child = Mock(side_effect=OSError("sensitive-environment-value"))
    monkeypatch.setattr(subprocess, "run", child)
    worker_loop_once(environment.session_factory)
    child.assert_called_once()
    with environment.session_factory() as session:
        job = session.get(Job, created["job_id"])
        batch = session.get(TargetCleanupBatch, created["batch_id"])
        assert job.status == "failed" and job.result_summary == ""
        assert "sensitive-environment-value" not in job.error_summary + batch.error_summary
        assert created["batch_id"] in job.error_summary
        assert batch.preview_status == "failed" if mode == "preview" else batch.execute_status == "partial"
    assert worker_loop_once(environment.session_factory) is None
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    if mode == "execute":
        assert payload["counts"]["not_processed"] == 2 and payload["counts"]["uncertain"] == 0


@pytest.mark.parametrize("return_code", (0, 1))
@pytest.mark.parametrize("second_status", ("skipped", "failed", "attempting", "uncertain", "pending"))
def test_partial_or_unknown_domain_fails_job_even_on_zero_exit(cleanup_environment, cleanup_client, monkeypatch,
                                                            return_code, second_status):
    environment = cleanup_environment
    created = create_execution(environment)
    child = Mock(side_effect=fake_cleanup_child(environment, statuses=("submitted", second_status), return_code=return_code))
    monkeypatch.setattr(subprocess, "run", child)
    assert worker_loop_once(environment.session_factory) == created["job_id"]
    child.assert_called_once()
    with environment.session_factory() as session:
        job = session.get(Job, created["job_id"])
        assert job.status == "failed" and job.result_summary == ""
        assert "no automatic retry" in job.error_summary.lower()
        assert f"/plan-cleanup?batch_id={created['batch_id']}" in job.error_summary
        assert job.progress_message == job.error_summary
    environment.identity.reset_mock()
    payload = cleanup_client.get(f"{BATCHES_URL}/{created['batch_id']}").json()
    expected_second = "uncertain" if second_status == "attempting" else "not_processed" if second_status == "pending" else second_status
    assert payload["counts"]["submitted"] == 1
    assert [item["status"] for item in payload["items"]] == ["submitted", expected_second]
    assert payload["execute_status"] == ("needs_review" if expected_second == "uncertain" else "partial")
    assert payload["can_execute"] is False
    for download_url in payload["downloads"].values():
        assert cleanup_client.get(download_url).status_code == 200
    assert worker_loop_once(environment.session_factory) is None
    environment.identity.assert_not_called()


def test_nonzero_exit_retains_all_submitted_but_generic_job_is_failed(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    created = create_execution(environment)
    child = Mock(side_effect=fake_cleanup_child(environment, return_code=2))
    monkeypatch.setattr(subprocess, "run", child)
    worker_loop_once(environment.session_factory)
    with environment.session_factory() as session:
        assert session.get(Job, created["job_id"]).status == "failed"
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["execute_status"] == "completed" and payload["counts"]["submitted"] == 2
    child.assert_called_once()


def test_empty_preview_completes_readonly_and_has_no_execute_job(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    created = service.create_preview(environment.session_factory, 4, "preview")
    child = Mock(side_effect=fake_cleanup_child(environment, preview_identifiers=()))
    monkeypatch.setattr(subprocess, "run", child)
    worker_loop_once(environment.session_factory)
    with environment.session_factory() as session:
        assert session.get(Job, created["job_id"]).status == "succeeded"
        assert session.get(TargetCleanupBatch, created["batch_id"]).execute_job_id is None
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["candidate_count"] == 0 and payload["execute_block_reason"] == "empty-preview"


def test_incomplete_preview_artifacts_remain_readable_on_zero_exit(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    created = service.create_preview(environment.session_factory, 4, "preview")
    monkeypatch.setattr(subprocess, "run", fake_cleanup_child(environment, complete=False))
    worker_loop_once(environment.session_factory)
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["preview_status"] == "incomplete" and payload["stop_reason"] == "page-limit"
    assert payload["can_execute"] is False and "scan_csv" in payload["downloads"]
    with environment.session_factory() as session:
        assert session.get(Job, created["job_id"]).status == "failed"


def test_zero_exit_without_script_claim_cannot_report_completed(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    created = create_execution(environment)
    child = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(subprocess, "run", child)
    worker_loop_once(environment.session_factory)
    with environment.session_factory() as session:
        assert session.get(Job, created["job_id"]).status == "failed"
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["execute_status"] == "partial" and payload["counts"]["not_processed"] == 2


def test_child_rechecks_store_identity_before_claim(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    created = create_execution(environment)
    environment.identity.return_value = {**environment.identity.return_value, "store_id": "changed"}
    child = Mock(side_effect=fake_cleanup_child(environment))
    monkeypatch.setattr(subprocess, "run", child)
    worker_loop_once(environment.session_factory)
    child.assert_called_once()
    with environment.session_factory() as session:
        job = session.get(Job, created["job_id"])
        assert job.status == "failed" and job.error_code == "store-identity-mismatch"
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["counts"]["not_processed"] == 2 and payload["counts"]["submitted"] == 0


def test_private_interpreter_rejection_prevents_spawn(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    created = service.create_preview(environment.session_factory, 4, "preview")
    guard = Mock(side_effect=RuntimeError("Wrong private interpreter"))
    child = Mock()
    monkeypatch.setattr(handler, "assert_private_interpreter", guard)
    monkeypatch.setattr(subprocess, "run", child)
    worker_loop_once(environment.session_factory)
    child.assert_not_called()
    with environment.session_factory() as session:
        assert session.get(Job, created["job_id"]).status == "failed"


def test_known_db_result_survives_stale_journal_and_child_failure(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    created = create_execution(environment)

    def stale_child(arguments, **options):
        service.begin_script(environment.session_factory, created["batch_id"], created["job_id"], "execute")
        report = write_synthetic_results(environment, created["batch_id"], ("submitted", "pending"))
        service.mark_item_attempting(environment.session_factory, created["batch_id"], "invite-1")
        service.save_item_result(environment.session_factory, created["batch_id"], report["items"][0])
        write_synthetic_results(environment, created["batch_id"], ("pending", "pending"))
        return SimpleNamespace(returncode=1)

    child = Mock(side_effect=stale_child)
    monkeypatch.setattr(subprocess, "run", child)
    worker_loop_once(environment.session_factory)
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["counts"]["submitted"] == 1 and payload["counts"]["not_processed"] == 1
    assert payload["items"][0]["action"] == "cancel-submitted"
    child.assert_called_once()


def test_exception_after_child_claim_recovers_journal_without_retry(cleanup_environment, monkeypatch):
    environment = cleanup_environment
    created = create_execution(environment)

    def interrupted_child(arguments, **options):
        service.begin_script(environment.session_factory, created["batch_id"], created["job_id"], "execute")
        write_synthetic_results(environment, created["batch_id"], ("submitted", "attempting"))
        raise OSError("sensitive process detail")

    child = Mock(side_effect=interrupted_child)
    monkeypatch.setattr(subprocess, "run", child)
    worker_loop_once(environment.session_factory)
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["counts"]["submitted"] == 1 and payload["counts"]["uncertain"] == 1
    assert payload["execute_status"] == "needs_review"
    with environment.session_factory() as session:
        job = session.get(Job, created["job_id"])
        assert job.status == "failed" and "sensitive process detail" not in job.error_summary
    child.assert_called_once()
    assert worker_loop_once(environment.session_factory) is None


def test_unreadable_binding_is_a_safe_handler_failure(monkeypatch):
    factory = Mock(side_effect=RuntimeError("sensitive database detail"))
    child = Mock()
    monkeypatch.setattr(subprocess, "run", child)
    with pytest.raises(HandlerFailure) as captured:
        handler.run_target_cleanup_job(str(uuid.uuid4()), factory)
    assert captured.value.error_code == "target-cleanup-binding-unavailable"
    assert "sensitive database detail" not in captured.value.summary
    child.assert_not_called()
