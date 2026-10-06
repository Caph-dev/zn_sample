"""Registered cleanup workers use fixed children and preserve domain evidence."""
from __future__ import annotations

import json
import re
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
from tests.test_target_invitation_navigation import RecoveryClock, RecoveryTransport
from tests.test_target_invitation_dom import make_page, make_row


class ClaimedBatchTransport(RecoveryTransport):
    """Drive actual navigation, scanning, locating, cancellation and restoration."""
    def __init__(self, environment, *, confirmation=False, failure=None):
        super().__init__(shop_id="shop-1")
        self.environment = environment
        self.confirmation = confirmation
        self.failure = failure
        self.created = None
        self.cancelled_ids = []
        self.confirmed_ids = []
        self.active_id = None
        self.ongoing = True
        self.page_size = "100/页"
        self.menu_ready = False
        self.confirmation_ready = False

    def execute(self, store_id, script, **options):
        from lib import target_invitation_dom as invitations

        if script == invitations.PREPARE_LIST_JS:
            return {"ok": True}
        if "count:rows.length" in script:
            self.events.append("extract")
            if self.failure == "stalled-last" and self.cancelled_ids:
                from tests.test_target_invitation_dom import make_covered_page
                return make_covered_page(1, 200, href=self.href, next_disabled=False)
            # Keep submitted IDs visible: submission never requires disappearance.
            return make_page(rows=[make_row(identifier, "2020/01/01") for identifier in ("invite-1", "invite-2")],
                             href=self.href, ongoing=self.ongoing, page_size=self.page_size, list_error=self.error)
        if script == invitations.CLEAR_CANCELLATION_ATTEMPT_JS:
            return {"ok": True}
        if "opened:true" in script and "const targetId =" in script:
            self.active_id = json.loads(re.search(r"const targetId = ([^\n]+);", script)[1])
            self.events.append("menu:" + self.active_id)
            self.menu_ready = True
            return {"ok": True, "opened": True}
        if "ready:menuItems.length" in script:
            return {"ok": True, "ready": self.menu_ready}
        if "clickedCancel:true" in script:
            assert options == {"retries": 0, "retry_timeout_expired": False}
            artifact_paths = service.artifact_paths(self.created["batch_id"])
            assert artifact_paths["backup_json"].is_file() and artifact_paths["backup_csv"].is_file()
            persisted = json.loads(artifact_paths["results_json"].read_text(encoding="utf-8"))
            assert next(item for item in persisted["items"] if item["invitation_id"] == self.active_id)["status"] == "attempting"
            payload = service.batch_payload(self.environment.session_factory, self.created["batch_id"])
            assert next(item for item in payload["items"] if item["invitation_id"] == self.active_id)["status"] == "attempting"
            assert self.active_id not in self.cancelled_ids
            self.cancelled_ids.append(self.active_id)
            self.events.append("cancel:" + self.active_id)
            self.ongoing = False
            self.page_size = "50/页"
            self.error = True
            self.confirmation_ready = self.confirmation
            if self.failure == "persistent":
                self.persistent = True
            if self.failure == "shop":
                self.after_retry_href = self.href.replace("shop-1", "changed-shop")
            if self.failure == "lost-receipt":
                raise RuntimeError("Synthetic lost cancellation response")
            return {"ok": True, "invitation_id": "wrong-id" if self.failure == "mismatched-receipt" else self.active_id,
                    "clickedCancel": True}
        if "ready:confirmations.length" in script:
            return {"ok": True, "ready": self.confirmation_ready}
        if "confirmed:true" in script:
            assert self.active_id not in self.confirmed_ids
            self.confirmed_ids.append(self.active_id)
            self.events.append("confirm:" + self.active_id)
            return {"ok": False} if self.failure == "confirmation" else {"ok": True, "confirmed": True}
        if "options.length" in script:
            if "options[0].click()" in script:
                self.events.append("option")
                self.page_size = "100/页"
                return {"ok": True, "set": True}
            return {"ok": True, "ready": True}
        if "ready:current ===" in script:
            return {"ok": True, "ready": self.page_size == "100/页", "current": self.page_size}
        context = invitations._shop_context(self.href)
        if "const expectedContext =" in script and context != (
                "affiliate.tiktokshopglobalselling.com", "shop-1", "US"):
            return {"ok": False, "reason": "list-context-changed"}
        if "has_pagination:hasPagination" in script:
            self.events.append("ready")
            return {"ok": True, "href": self.href, "ready": not self.error, "ongoing": self.ongoing}
        if "const ongoingTab =" in script and "shell_ready" not in script:
            self.events.append("ongoing")
            clicked = not self.ongoing
            self.ongoing = True
            return {"ok": True, "href": self.href, "clicked": clicked, "already": not clicked}
        if "const want = 100;" in script:
            self.events.append("size")
            return {"ok": True, "href": self.href, "already": self.page_size == "100/页",
                    "opened": self.page_size != "100/页"}
        return super().execute(store_id, script, **options)


def install_claimed_transport(monkeypatch, transport):
    from lib import target_invitation_dom as invitations, target_invitation_navigation as navigation, zclaw
    clock = RecoveryClock()
    monkeypatch.setattr(invitations.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(invitations.time, "sleep", clock.sleep)
    monkeypatch.setattr(invitations, "zclaw_exec", transport.execute)
    monkeypatch.setattr(navigation, "zclaw_exec", transport.execute)
    monkeypatch.setattr(zclaw, "zclaw_invoke", Mock(side_effect=AssertionError("No real CLI allowed")))
    return clock


@pytest.mark.parametrize("persistent", (False, True))
def test_real_claimed_preview_reaches_error_recovery_before_scanning(cleanup_environment, monkeypatch, persistent):
    import cleanup_target_plans as script
    environment = cleanup_environment
    transport = ClaimedBatchTransport(environment)
    transport.persistent = persistent
    install_claimed_transport(monkeypatch, transport)
    created = service.create_preview(environment.session_factory, 4, "real-preview")
    set_job_status(environment, created["job_id"], "running")
    if persistent:
        with pytest.raises(RuntimeError, match="not recovered"):
            script.run_batch(environment.session_factory, batch_id=created["batch_id"], job_id=created["job_id"], mode="preview")
    else:
        assert script.run_batch(environment.session_factory, batch_id=created["batch_id"], job_id=created["job_id"], mode="preview") == 0
    set_job_status(environment, created["job_id"], "failed" if persistent else "succeeded")
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["preview_status"] == ("failed" if persistent else "completed")
    assert transport.retry_count == (3 if persistent else 1)
    assert not transport.cancelled_ids and not transport.confirmed_ids
    if persistent:
        assert not payload["can_execute"] and not payload["snapshot_sha256"]
        with pytest.raises(service.TargetCleanupServiceError):
            service.create_execution(environment.session_factory, created["batch_id"], "y", "blocked")
    else:
        assert payload["candidate_count"] == 2 and payload["can_execute"]
        assert transport.events.index("retry") < transport.events.index("extract")
        assert service.artifact_paths(created["batch_id"])["snapshot"].is_file()


@pytest.mark.parametrize("confirmation", (False, True))
@pytest.mark.parametrize("failure", (None, "persistent", "shop", "lost-receipt", "mismatched-receipt", "confirmation", "stalled-last"))
def test_real_claimed_execution_preserves_receipts_and_stops_unknown_writes(cleanup_environment, monkeypatch,
                                                                          confirmation, failure):
    import cleanup_target_plans as script
    from lib import target_invitation_dom as invitations
    environment = cleanup_environment
    created = create_execution(environment)
    transport = ClaimedBatchTransport(environment, confirmation=confirmation, failure=failure)
    # Frozen synthetic preview uses cutoff-1, so preserve its actual immutable displayed dates.
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    modified_by_id = {item["invitation_id"]: item["last_modified"] for item in payload["items"]}
    original_execute = transport.execute
    def transport_with_frozen_dates(store_id, source, **options):
        response = original_execute(store_id, source, **options)
        if "count:rows.length" in source and not (failure == "stalled-last" and transport.cancelled_ids):
            for row in response["rows"]:
                row["last_modified"] = modified_by_id[row["invitation_id"]]
        return response
    transport.execute = transport_with_frozen_dates
    transport.created = created
    install_claimed_transport(monkeypatch, transport)
    set_job_status(environment, created["job_id"], "running")
    captures = []
    real_cancel = invitations.cancel_invitation_by_id
    def capture_cancel(*arguments, **options):
        result = real_cancel(*arguments, **options)
        captures.append(result)
        return result
    monkeypatch.setattr(invitations, "cancel_invitation_by_id", capture_cancel)
    uncertain = failure is not None and (failure != "confirmation" or confirmation)
    exit_code = script.run_batch(environment.session_factory, batch_id=created["batch_id"],
                                  job_id=created["job_id"], mode="execute", execute=True, yes=True)
    assert exit_code == int(uncertain)
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert [item["status"] for item in payload["items"]] == (
        ["uncertain", "not_processed"] if uncertain else ["submitted", "submitted"])
    assert transport.cancelled_ids == (["invite-1"] if uncertain else ["invite-1", "invite-2"])
    expected_confirmed = transport.cancelled_ids if confirmation and failure not in {"lost-receipt", "mismatched-receipt"} else []
    assert transport.confirmed_ids == expected_confirmed
    if failure == "stalled-last":
        write_position = transport.events.index("cancel:invite-1")
        assert transport.events[write_position:].count("last") == 1
        assert "menu:invite-2" not in transport.events
    assert all(result["gone"] is None for result in captures)
    if not uncertain:
        assert all(result["confirmed"] is confirmation for result in captures)
        assert transport.events.count("option") == 2
        assert transport.retry_count == 3  # initial recovery plus exactly one per cancellation
        first_cancel = transport.events.index("cancel:invite-1")
        next_menu = transport.events.index("menu:invite-2")
        restored_events = transport.events[first_cancel + 1:next_menu]
        assert restored_events.index("ongoing") < restored_events.index("retry") < restored_events.index("size") < restored_events.index("last")
    elif failure == "persistent":
        assert transport.retry_count == 4  # initial success + bounded three postwrite retries
    elif failure in {"lost-receipt", "mismatched-receipt", "confirmation"}:
        assert transport.retry_count == 2  # restoration cannot upgrade an untrusted receipt
    persisted = json.loads(service.artifact_paths(created["batch_id"])["results_json"].read_text(encoding="utf-8"))
    assert [item["status"] for item in persisted["items"]] == [item["status"] for item in payload["items"]]
    # Both persisted evidence and the one-time server claim survive a retry attempt.
    with pytest.raises(service.TargetCleanupServiceError):
        script.run_batch(environment.session_factory, batch_id=created["batch_id"],
                           job_id=created["job_id"], mode="execute", execute=True, yes=True)
    assert transport.cancelled_ids == (["invite-1"] if uncertain else ["invite-1", "invite-2"])
    set_job_status(environment, created["job_id"], "failed" if uncertain else "succeeded")
    next_preview = service.create_preview(environment.session_factory, 4, "new-preview")
    finish_synthetic_preview(environment, next_preview)
    with pytest.raises(service.TargetCleanupServiceError) as historical:
        service.create_execution(environment.session_factory, next_preview["batch_id"], "y", "new-execution")
    assert historical.value.code == "historical-write-requires-review"


def test_real_execution_navigation_failure_never_starts_a_write(cleanup_environment, monkeypatch):
    import cleanup_target_plans as script
    environment = cleanup_environment
    created = create_execution(environment)
    transport = ClaimedBatchTransport(environment)
    transport.created = created
    transport.persistent = True
    install_claimed_transport(monkeypatch, transport)
    def run_real_child(arguments, **options):
        with pytest.raises(RuntimeError, match="not recovered"):
            script.run_batch(environment.session_factory, batch_id=created["batch_id"],
                               job_id=created["job_id"], mode="execute", execute=True, yes=True)
        return SimpleNamespace(returncode=1)
    child = Mock(side_effect=run_real_child)
    monkeypatch.setattr(subprocess, "run", child)
    assert worker_loop_once(environment.session_factory) == created["job_id"]
    child.assert_called_once()
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["counts"]["not_processed"] == 2
    assert payload["counts"]["uncertain"] == payload["counts"]["submitted"] == 0
    assert not transport.cancelled_ids and not transport.confirmed_ids
    assert transport.retry_count == 3
    assert not service.artifact_paths(created["batch_id"])["backup_json"].exists()


@pytest.mark.parametrize("journal", ("missing", "invalid"))
def test_exception_after_entering_execution_never_uses_prewrite_closure(cleanup_environment, monkeypatch, journal):
    import cleanup_target_plans as script
    environment = cleanup_environment
    created = create_execution(environment)
    transport = ClaimedBatchTransport(environment)
    install_claimed_transport(monkeypatch, transport)
    set_job_status(environment, created["job_id"], "running")
    def crash_at_execution_boundary(*arguments, **options):
        if journal == "invalid":
            service.artifact_paths(created["batch_id"])["results_json"].write_bytes(b"corrupt execution journal")
        raise RuntimeError("Synthetic exception after execution phase began")
    execute = Mock(side_effect=crash_at_execution_boundary)
    monkeypatch.setattr(script, "execute_snapshot", execute)
    closure = Mock(side_effect=AssertionError("No prewrite attestation after execution began"))
    monkeypatch.setattr(service, "finish_prewrite_navigation_failure", closure)
    with pytest.raises(RuntimeError, match="execution phase began"):
        script.run_batch(environment.session_factory, batch_id=created["batch_id"],
                           job_id=created["job_id"], mode="execute", execute=True, yes=True)
    execute.assert_called_once()
    closure.assert_not_called()
    payload = service.batch_payload(environment.session_factory, created["batch_id"])
    assert payload["counts"]["uncertain"] == 2 and payload["counts"]["not_processed"] == 0
    assert payload["execute_status"] == "needs_review"
    assert not transport.cancelled_ids


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
