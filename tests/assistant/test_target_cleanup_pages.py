"""Console bootstrap contracts backed by synthetic domain data, with no store access."""
from __future__ import annotations

import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.database.models import Job, TargetCleanupBatch
from assistant.services import target_cleanup_service as service
from tests.assistant.console_payload import console_payload
from tests.assistant.test_target_cleanup_api import (
    cleanup_client, cleanup_environment, finish_synthetic_preview,
    set_job_status, write_synthetic_results,
)


@pytest.fixture(autouse=True)
def forbid_page_readiness_probes(monkeypatch):
    boundaries = []
    for module, name in (
        ("assistant.web.routes", "_request_store_summary"),
        ("assistant.web.routes", "request_safe_store_summary"),
        ("lib.zclaw", "probe_store_page"),
    ):
        boundary = Mock(side_effect=AssertionError("Cleanup page must not inspect a store"))
        monkeypatch.setattr(f"{module}.{name}", boundary)
        boundaries.append(boundary)
    yield
    for boundary in boundaries:
        boundary.assert_not_called()


def seed_batch(environment, state="normal", identifiers=("invite-1", "invite-2")):
    created = service.create_preview(environment.session_factory, 4, f"preview-{uuid.uuid4()}")
    if state == "running":
        set_job_status(environment, created["job_id"], "running")
        service.begin_script(environment.session_factory, created["batch_id"], created["job_id"], "preview")
        return created
    finish_synthetic_preview(environment, created, complete=state != "incomplete",
                             identifiers=() if state == "empty" else identifiers)
    if state == "expired":
        with environment.session_factory() as session:
            session.get(TargetCleanupBatch, created["batch_id"]).expires_at = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            )
            session.commit()
    if state in {"partial", "needs_review", "completed", "execute_running"}:
        execution = service.create_execution(environment.session_factory, created["batch_id"], "y", f"execute-{uuid.uuid4()}")
        set_job_status(environment, execution["job_id"], "running")
        service.begin_script(environment.session_factory, created["batch_id"], execution["job_id"], "execute")
        if state != "execute_running":
            statuses = {"partial": ("submitted", "skipped"),
                        "needs_review": ("submitted", "uncertain"),
                        "completed": ("submitted", "submitted")}[state]
            write_synthetic_results(environment, created["batch_id"], statuses)
            service.finish_execution(environment.session_factory, created["batch_id"], state)
            with environment.session_factory() as session:
                job = session.get(Job, execution["job_id"])
                job.status = "succeeded" if state == "completed" else "failed"
                job.result_summary = ""  # Page/report evidence cannot depend on worker output.
                session.commit()
    return created


@pytest.mark.parametrize("state,preview_status,execute_status,block", (
    ("normal", "completed", "unclaimed", ""),
    ("incomplete", "incomplete", "unclaimed", "incomplete-preview"),
    ("empty", "completed", "unclaimed", "empty-preview"),
    ("expired", "completed", "unclaimed", "expired-preview"),
    ("running", "running", "unclaimed", "preview-not-completed"),
    ("execute_running", "completed", "running", "batch-already-consumed"),
    ("partial", "completed", "partial", "batch-already-consumed"),
    ("needs_review", "completed", "needs_review", "batch-already-consumed"),
    ("completed", "completed", "completed", "batch-already-consumed"),
))
def test_bootstrap_domain_states_are_read_only(cleanup_client, cleanup_environment, state,
                                               preview_status, execute_status, block):
    created = seed_batch(cleanup_environment, state)
    cleanup_environment.identity.reset_mock()
    response = cleanup_client.get("/plan-cleanup", params={"batch_id": created["batch_id"]})
    assert response.status_code == 200
    payload = console_payload(response)
    assert payload["page"] == "plan_cleanup"
    data = payload["data"]
    assert data["default_months"] == 4 and data["prepare_href"] == "/prepare"
    assert data["batch_id"] == created["batch_id"]
    assert data["database_ready"] is True and data["read_error"] == ""
    assert "store_summary" not in data and "operator_groups" not in data
    batch = data["batch"]
    assert batch["preview_status"] == preview_status
    assert batch["execute_status"] == execute_status
    assert batch["can_execute"] is (not block)
    assert batch["execute_block_reason"] == block
    assert batch["jobs"]["preview"]["job_id"] == created["job_id"]
    assert batch["frozen"]["months"] == 4
    assert batch["store_name"] == "Synthetic store"
    if state != "running":
        assert batch["stop_reason"] == ("page-limit" if state == "incomplete"
                                        else "empty-list" if state == "empty" else "first-page")
        assert batch["nonzero_count"] == (0 if state == "empty" else 2)
        assert {"scan_csv", "candidates_csv"} <= batch["downloads"].keys()
        for item in batch["items"]:
            assert item["last_modified"] == item["modified_date"]
            assert item["accepted_count"] == 3 and item["promoted_count"] == 1
    if state in {"partial", "needs_review", "completed"}:
        assert batch["results_csv_ready"] is True
        assert batch["counts"]["submitted"] == (2 if state == "completed" else 1)
        result_download = cleanup_client.get(batch["downloads"]["results_csv"])
        assert result_download.status_code == 200
        assert "submitted" in result_download.text
        assert batch["jobs"]["execute"]["status"] in {"failed", "succeeded"}
    cleanup_environment.identity.assert_not_called()


def test_open_refresh_and_bounded_paging_never_create_jobs(cleanup_client, cleanup_environment):
    created = seed_batch(cleanup_environment, identifiers=tuple(f"invite-{index}" for index in range(105)))
    cleanup_environment.identity.reset_mock()
    for _attempt in range(2):
        data = console_payload(cleanup_client.get("/plan-cleanup", params={"batch_id": created["batch_id"]}))["data"]
        assert data["batch"]["total"] == 105
        assert len(data["batch"]["items"]) == 100
        assert data["batch"]["offset"] == 0
        assert data["recent_batches"]["limit"] == 20
    page = cleanup_client.get(f"/api/target-cleanup/batches/{created['batch_id']}", params={"offset": 100, "limit": 100}).json()
    assert len(page["items"]) == 5 and page["total"] == 105
    with cleanup_environment.session_factory() as session:
        assert len(session.query(Job).all()) == 1
    cleanup_environment.identity.assert_not_called()


@pytest.mark.parametrize("identifier", ("not-a-uuid", str(uuid.uuid4())))
def test_unknown_batch_keeps_identifier_and_recent_history(cleanup_client, cleanup_environment, identifier):
    created = seed_batch(cleanup_environment)
    cleanup_environment.identity.reset_mock()
    data = console_payload(cleanup_client.get("/plan-cleanup", params={"batch_id": identifier}))["data"]
    assert data["batch_id"] == identifier and data["batch"] is None
    assert data["read_error"]
    assert data["recent_batches"]["batches"][0]["batch_id"] == created["batch_id"]
    cleanup_environment.identity.assert_not_called()


def test_page_without_database_is_a_read_only_shell(cleanup_client):
    cleanup_client.app.state.session_factory = None
    response = cleanup_client.get("/plan-cleanup")
    assert response.status_code == 200
    data = console_payload(response)["data"]
    assert data["database_ready"] is False and data["read_error"]
    assert data["batch"] is None and data["default_months"] == 4
    assert "/static/console/assets/index.js" in response.text
    assert "data-confirm-dialog" in response.text and "data-task-panel" in response.text


def test_ninth_navigation_is_shared_between_console_and_auto_approval(cleanup_client):
    source = (PROJECT_ROOT / "frontend/src/components/AppNavigation.tsx").read_text(encoding="utf-8")
    destinations = re.findall(r"\{href: '([^']+)', label: '([^']+)'\}", source)
    assert destinations == [("/", "总览"), ("/prepare", "运行准备"), ("/auto-approval", "自动批准"),
                            ("/followups", "达人跟进"), ("/plan-cleanup", "计划清理"),
                            ("/shipments", "物流"), ("/jobs", "任务"), ("/reports", "报表"),
                            ("/diagnostics", "诊断")]
    for relative_path in ("frontend/src/console/ConsoleApp.tsx", "frontend/src/App.tsx"):
        assert "AppNavigation" in (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    assert console_payload(cleanup_client.get("/plan-cleanup"))["page"] == "plan_cleanup"
    auto_page = cleanup_client.get("/auto-approval")
    assert auto_page.status_code == 200 and 'id="auto-approval-bootstrap"' in auto_page.text


def test_new_job_labels_are_meaningful_in_existing_job_pages(cleanup_client, cleanup_environment):
    created = seed_batch(cleanup_environment, "execute_running")
    with cleanup_environment.session_factory() as session:
        batch = session.get(TargetCleanupBatch, created["batch_id"])
        execute_job_id = batch.execute_job_id
    listing = console_payload(cleanup_client.get("/jobs"))["data"]["rows"]
    labels = {row["job_type"]: row["job_label"] for row in listing}
    assert labels["target_cleanup_preview"] == "计划清理 · 只读预览"
    assert labels["target_cleanup_execute"] == "计划清理 · 提交取消操作"
    detail = console_payload(cleanup_client.get(f"/jobs/{execute_job_id}"))["data"]
    assert detail["is_active"] is True and detail["job_label"] == labels["target_cleanup_execute"]
