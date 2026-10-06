"""Real-app form gates, durable recovery and local CSV downloads, entirely offline."""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant import paths
from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import Base, Job, TargetCleanupBatch
from assistant.services import target_cleanup_service as service
from lib import target_plan_cleanup as core

ORIGIN = {"Origin": "http://127.0.0.1:8765"}
PREVIEWS_URL = "/api/target-cleanup/previews"
BATCHES_URL = "/api/target-cleanup/batches"


@pytest.fixture
def cleanup_environment(tmp_path, monkeypatch):
    """Shared synthetic batch support for API and handler integration tests."""
    root = tmp_path / "user-data"
    root.mkdir()
    (root / "logs").mkdir()
    engine = create_database_engine(root / "assistant.sqlite3")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    identity = Mock(return_value={
        "store_id": "store-1", "store_name": "Synthetic store",
        "shop_id": "shop-1", "shop_region": "US",
    })
    monkeypatch.setattr(paths, "user_data_dir", lambda: root)
    monkeypatch.setattr(paths, "_release_manifest", lambda: None)
    monkeypatch.setattr(service, "ensure_user_dirs", lambda: root)
    monkeypatch.setattr(service, "resolve_running_identity", identity)
    # Identity is synthetic; any unmocked transport or store control is a failure.
    from lib import zclaw
    forbidden_calls = []
    for name in ("zclaw_exec", "list_running_stores", "open_store", "close_store"):
        boundary = Mock(side_effect=AssertionError("Unmocked platform boundary"))
        monkeypatch.setattr(zclaw, name, boundary)
        forbidden_calls.append(boundary)
    environment = SimpleNamespace(root=root, engine=engine, session_factory=session_factory, identity=identity)
    try:
        yield environment
        for boundary in forbidden_calls:
            boundary.assert_not_called()
    finally:
        engine.dispose()


@pytest.fixture
def cleanup_client(cleanup_environment):
    application = create_app(port=8765)
    application.state.session_factory = cleanup_environment.session_factory
    with TestClient(application, base_url="http://127.0.0.1:8765") as client:
        yield client


def set_job_status(environment, job_id: str, status: str) -> None:
    with environment.session_factory() as session:
        job = session.get(Job, job_id)
        job.status = status
        job.result_summary = "worker-owned-output-not-an-input"
        session.commit()


def finish_synthetic_preview(environment, created, *, identifiers=("invite-1", "invite-2"),
                             complete=True, terminal_job=True):
    set_job_status(environment, created["job_id"], "running")
    request = service.begin_script(environment.session_factory, created["batch_id"], created["job_id"], "preview")
    modified_date = date.fromisoformat(request["frozen"]["cutoff"]) - timedelta(days=1)
    snapshot = core.create_snapshot(request["frozen"], {
        "rows": [{
            "invitation_id": identifier, "name": "Synthetic " + identifier,
            "last_modified": modified_date.isoformat(),
            "accepted_count": 3, "promoted_count": 1, "invited_count": 5,
        } for identifier in identifiers],
        "scan_complete": complete,
        "stop_reason": ("first-page" if identifiers else "empty-list") if complete else "page-limit",
        "pages_scanned": 1,
    })
    core.persist_preview(snapshot, service.cleanup_directory())
    service.finish_preview(environment.session_factory, created["batch_id"], snapshot)
    if terminal_job:
        set_job_status(environment, created["job_id"], "succeeded" if complete else "failed")
    return snapshot


def write_synthetic_results(environment, batch_id, statuses):
    payload = service.batch_payload(environment.session_factory, batch_id)
    timestamp = datetime.now(timezone.utc).isoformat()
    items = [{
        **item, "status": status, "action": "cancel-submitted" if status == "submitted" else status,
        "summary": "Synthetic " + status,
        "attempted_at": timestamp if status in {"submitted", "attempting", "uncertain"} else "",
        "returned_at": timestamp if status in {"submitted", "skipped", "failed"} else "",
    } for item, status in zip(payload["items"], statuses, strict=True)]
    report = {"schema_version": core.SCHEMA_VERSION, "batch_id": batch_id,
              "snapshot_sha256": payload["snapshot_sha256"], "items": items}
    artifact_paths = service.artifact_paths(batch_id)
    core.atomic_write_json(artifact_paths["results_json"], report)
    core.write_csv_report(artifact_paths["results_csv"], items, results=True)
    return report


def post_preview(client, *, months="4", key="preview-key"):
    return client.post(PREVIEWS_URL, headers=ORIGIN,
                       files={"months": (None, months), "idempotency_key": (None, key)})


@pytest.mark.parametrize("months", ("2", "4"))
def test_form_preview_lost_response_returns_same_durable_job(cleanup_client, cleanup_environment, months):
    first = post_preview(cleanup_client, months=months).json()
    duplicate = post_preview(cleanup_client, months=months).json()
    assert first["deduplicated"] is False
    assert duplicate == {**first, "deduplicated": True}
    assert first["batch_url"] == f"/plan-cleanup?batch_id={first['batch_id']}"
    recovered = cleanup_client.get(BATCHES_URL, params={"idempotency_key": "preview-key"}).json()
    assert recovered["total"] == 1 and recovered["batches"][0]["preview_job_id"] == first["job_id"]
    cleanup_environment.identity.assert_called_once()
    with cleanup_environment.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.get(TargetCleanupBatch, first["batch_id"]).months == int(months)


def test_same_key_different_months_conflicts_without_identity(cleanup_client, cleanup_environment):
    first = post_preview(cleanup_client).json()
    cleanup_environment.identity.reset_mock()
    response = post_preview(cleanup_client, months="2")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "idempotency-conflict"
    cleanup_environment.identity.assert_not_called()
    assert cleanup_client.get(BATCHES_URL).json()["batches"][0]["batch_id"] == first["batch_id"]


@pytest.mark.parametrize("months", ("02", "+2", "2.0", " 2", "4 ", "3", "0", "true", "NaN"))
def test_invalid_months_rejected_before_service(cleanup_client, cleanup_environment, months):
    with patch.object(service, "create_preview") as create:
        response = post_preview(cleanup_client, months=months)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid-months"
    create.assert_not_called()
    cleanup_environment.identity.assert_not_called()


@pytest.mark.parametrize("phase", ("preview", "execute"))
@pytest.mark.parametrize("invalid_form", ("unknown", "repeated", "missing", "file", "empty", "json", "query"))
def test_form_whitelist_blocks_invalid_fields_before_service(cleanup_client, cleanup_environment, phase, invalid_form):
    batch_id = str(uuid.uuid4())
    endpoint = PREVIEWS_URL if phase == "preview" else f"{BATCHES_URL}/{batch_id}/execute"
    first_name, first_value = ("months", "4") if phase == "preview" else ("confirmation", "y")
    fields = [(first_name, (None, first_value)), ("idempotency_key", (None, "key"))]
    if invalid_form == "unknown":
        fields.append(("store_id", (None, "arbitrary-store")))
    elif invalid_form == "repeated":
        fields.append((first_name, (None, first_value)))
    elif invalid_form == "missing":
        fields.pop()
    elif invalid_form == "file":
        fields[0] = (first_name, ("uploaded.csv", b"4", "text/csv"))
    elif invalid_form == "empty":
        fields[1] = ("idempotency_key", (None, " "))
    elif invalid_form == "query":
        endpoint += "?store_id=arbitrary-store"
    with patch.object(service, "create_preview") as preview, patch.object(service, "create_execution") as execution:
        if invalid_form == "json":
            response = cleanup_client.post(endpoint, headers=ORIGIN, json={first_name: first_value, "idempotency_key": "key"})
        else:
            response = cleanup_client.post(endpoint, headers=ORIGIN, files=fields)
    assert response.status_code == (415 if invalid_form == "json" else 400)
    preview.assert_not_called()
    execution.assert_not_called()
    cleanup_environment.identity.assert_not_called()


@pytest.mark.parametrize("content", ("months=4&months=4&idempotency_key=key", "months=4&idempotency_key=key&command=evil"))
def test_urlencoded_repeated_and_unknown_fields_are_not_collapsed(cleanup_client, content):
    with patch.object(service, "create_preview") as create:
        response = cleanup_client.post(PREVIEWS_URL, content=content,
                                       headers={**ORIGIN, "Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 400
    create.assert_not_called()


@pytest.mark.parametrize("phase", ("preview", "execute"))
@pytest.mark.parametrize("headers,expected_status", (({}, 403), ({"Origin": "http://evil.example"}, 403),
                                                       ({**ORIGIN, "Host": "evil.example"}, 400)))
def test_native_app_origin_and_host_gates(cleanup_client, phase, headers, expected_status):
    endpoint = PREVIEWS_URL if phase == "preview" else f"{BATCHES_URL}/{uuid.uuid4()}/execute"
    with patch.object(service, "create_preview") as preview, patch.object(service, "create_execution") as execution:
        response = cleanup_client.post(endpoint, headers=headers, data={"months": "4", "idempotency_key": "key"})
    assert response.status_code == expected_status
    preview.assert_not_called()
    execution.assert_not_called()


def test_execute_lost_response_and_key_conflicts_use_real_service(cleanup_client, cleanup_environment):
    preview = post_preview(cleanup_client).json()
    finish_synthetic_preview(cleanup_environment, preview)
    endpoint = f"{BATCHES_URL}/{preview['batch_id']}/execute"
    fields = {"confirmation": " Y ", "idempotency_key": "execute-key"}
    cleanup_environment.identity.reset_mock()
    first = cleanup_client.post(endpoint, headers=ORIGIN, data=fields)
    assert first.status_code == 200
    duplicate = cleanup_client.post(endpoint, headers=ORIGIN, data=fields)
    assert duplicate.json() == {**first.json(), "deduplicated": True}
    cleanup_environment.identity.assert_called_once()
    assert cleanup_client.get(BATCHES_URL, params={"idempotency_key": "execute-key"}).json()["total"] == 1
    for request_fields, expected_code in (({**fields, "idempotency_key": "another-key"}, "batch-already-consumed"),
                                          ({**fields, "idempotency_key": "preview-key"}, "idempotency-conflict")):
        response = cleanup_client.post(endpoint, headers=ORIGIN, data=request_fields)
        assert response.status_code == 409 and response.json()["detail"]["code"] == expected_code
    response = post_preview(cleanup_client, key="execute-key")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "idempotency-conflict"
    response = cleanup_client.post(f"{BATCHES_URL}/{uuid.uuid4()}/execute", headers=ORIGIN, data=fields)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "idempotency-conflict"


@pytest.mark.parametrize("confirmation", ("yes", "no", "Y Y"))
def test_only_y_confirms_execution(cleanup_client, cleanup_environment, confirmation):
    response = cleanup_client.post(f"{BATCHES_URL}/{uuid.uuid4()}/execute", headers=ORIGIN,
                                   data={"confirmation": confirmation, "idempotency_key": "key"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "confirmation-required"
    cleanup_environment.identity.assert_not_called()


@pytest.mark.parametrize("phase", ("preview", "execute"))
def test_busy_api_rejects_before_identity_probe(cleanup_client, cleanup_environment, phase):
    if phase == "execute":
        preview = post_preview(cleanup_client).json()
        finish_synthetic_preview(cleanup_environment, preview)
        endpoint = f"{BATCHES_URL}/{preview['batch_id']}/execute"
        fields = {"confirmation": "y", "idempotency_key": "execution"}
    else:
        endpoint = PREVIEWS_URL
        fields = {"months": "2", "idempotency_key": "new-preview"}
    with cleanup_environment.session_factory() as session:
        session.add(Job(job_type="operator_tracking", status="pending", store_id="another-store"))
        session.commit()
    cleanup_environment.identity.reset_mock()
    response = cleanup_client.post(endpoint, headers=ORIGIN, data=fields)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "store-busy"
    cleanup_environment.identity.assert_not_called()


@pytest.mark.parametrize("specific", (False, True))
@pytest.mark.parametrize("parameter,value", (("offset", "-1"), ("limit", "0"), ("limit", "101"), ("offset", "bad")))
def test_get_pagination_rejected_before_recovery(cleanup_client, specific, parameter, value):
    endpoint = f"{BATCHES_URL}/{uuid.uuid4()}" if specific else BATCHES_URL
    with patch.object(service, "recover_batch") as recover, patch.object(service, "list_batches") as listing:
        response = cleanup_client.get(endpoint, params={parameter: value})
    assert response.status_code == 422
    recover.assert_not_called()
    listing.assert_not_called()


def test_list_maximum_and_key_length_are_bounded(cleanup_client):
    assert cleanup_client.get(BATCHES_URL, params={"limit": 21}).status_code == 422
    assert cleanup_client.get(BATCHES_URL, params={"idempotency_key": "x" * 129}).status_code == 422


def test_item_pages_and_download_links_are_local_and_bounded(cleanup_client, cleanup_environment):
    created = post_preview(cleanup_client).json()
    identifiers = tuple(f"invite-{position:03d}" for position in range(105))
    finish_synthetic_preview(cleanup_environment, created, identifiers=identifiers)
    cleanup_environment.identity.reset_mock()
    first = cleanup_client.get(f"{BATCHES_URL}/{created['batch_id']}").json()
    second = cleanup_client.get(f"{BATCHES_URL}/{created['batch_id']}", params={"offset": 100, "limit": 100}).json()
    assert len(first["items"]) == 100 and len(second["items"]) == 5 and first["total"] == 105
    assert first["items"][0]["invitation_id"] == identifiers[0]
    assert second["items"][0]["invitation_id"] == identifiers[100]
    assert first["can_execute"] is True
    assert set(first["downloads"]) == {"scan_csv", "candidates_csv"}
    for name, download_url in first["downloads"].items():
        response = cleanup_client.get(download_url)
        assert response.status_code == 200
        assert response.content == service.artifact_paths(created["batch_id"])[name].read_bytes()
        assert "snapshot.json" not in download_url
    cleanup_environment.identity.assert_not_called()


def test_get_recovers_abandoned_attempts_and_keeps_known_results(cleanup_client, cleanup_environment):
    preview = post_preview(cleanup_client).json()
    finish_synthetic_preview(cleanup_environment, preview, identifiers=("one", "two", "three"))
    execution = service.create_execution(cleanup_environment.session_factory, preview["batch_id"], "y", "execute")
    set_job_status(cleanup_environment, execution["job_id"], "running")
    service.begin_script(cleanup_environment.session_factory, preview["batch_id"], execution["job_id"], "execute")
    write_synthetic_results(cleanup_environment, preview["batch_id"], ("submitted", "attempting", "pending"))
    set_job_status(cleanup_environment, execution["job_id"], "interrupted")
    cleanup_environment.identity.reset_mock()
    payload = cleanup_client.get(f"{BATCHES_URL}/{preview['batch_id']}").json()
    assert payload["execute_status"] == "needs_review"
    assert [item["status"] for item in payload["items"]] == ["submitted", "uncertain", "not_processed"]
    assert payload["can_execute"] is False
    assert cleanup_client.get(payload["downloads"]["results_csv"]).status_code == 200
    assert cleanup_client.get(f"{BATCHES_URL}/{preview['batch_id']}").json()["counts"] == payload["counts"]
    cleanup_environment.identity.assert_not_called()


def test_recent_list_recovers_only_visible_batch_ids(cleanup_client, cleanup_environment):
    first = post_preview(cleanup_client, key="first").json()
    set_job_status(cleanup_environment, first["job_id"], "cancelled")
    second = post_preview(cleanup_client, key="second").json()
    set_job_status(cleanup_environment, second["job_id"], "cancelled")
    cleanup_environment.identity.reset_mock()
    payload = cleanup_client.get(BATCHES_URL, params={"idempotency_key": "second", "limit": 1}).json()
    assert payload["batches"][0]["preview_status"] == "failed"
    with cleanup_environment.session_factory() as session:
        assert session.get(TargetCleanupBatch, first["batch_id"]).preview_status == "queued"
    cleanup_environment.identity.assert_not_called()


def test_empty_preview_is_successful_but_cannot_create_execute_job(cleanup_client, cleanup_environment):
    created = post_preview(cleanup_client).json()
    finish_synthetic_preview(cleanup_environment, created, identifiers=())
    response = cleanup_client.post(f"{BATCHES_URL}/{created['batch_id']}/execute", headers=ORIGIN,
                                   data={"confirmation": "y", "idempotency_key": "execute"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "empty-preview"
    with cleanup_environment.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1


def test_missing_and_invalid_batch_errors_are_native_service_errors(cleanup_client):
    response = cleanup_client.get(f"{BATCHES_URL}/{uuid.uuid4()}")
    assert response.status_code == 404 and response.json()["detail"]["code"] == "batch-not-found"
    response = cleanup_client.get(f"{BATCHES_URL}/not-a-uuid")
    assert response.status_code == 400 and response.json()["detail"]["code"] == "invalid-batch-id"
