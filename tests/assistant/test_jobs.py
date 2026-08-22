from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import Base, Job, Store
from assistant.jobs.locks import (
    clear_cancellation,
    install_ziniao_busy_guard,
    is_cancellation_requested,
)
from assistant.jobs.progress import append_event, list_events
from assistant.jobs.worker import (
    mark_stale_jobs_interrupted,
    worker_loop_once,
)


class JobTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.engine = create_database_engine(self.root / "assistant.sqlite3")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def add_job(
        self,
        *,
        job_type: str = "environment_check",
        status: str = "pending",
        store_id: str | None = None,
        heartbeat_at: datetime | None = None,
    ) -> str:
        with self.session_factory() as session:
            job = Job(
                job_type=job_type,
                store_id=store_id,
                status=status,
                heartbeat_at=heartbeat_at,
                requested_by="test",
            )
            session.add(job)
            session.commit()
            return job.id

    def get_job(self, job_id: str) -> Job:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            self.assertIsNotNone(job)
            session.expunge(job)
            return job


class JobWorkerTests(JobTestCase):
    def test_stale_running_becomes_interrupted(self) -> None:
        stale_job_id = self.add_job(
            status="running",
            heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=61),
        )
        pending_job_id = self.add_job(status="pending")

        self.assertEqual(mark_stale_jobs_interrupted(self.session_factory), 1)
        self.assertEqual(self.get_job(stale_job_id).status, "interrupted")
        self.assertEqual(self.get_job(pending_job_id).status, "pending")

    def test_unknown_job_type_fails_closed(self) -> None:
        job_id = self.add_job(job_type="unregistered")

        self.assertEqual(worker_loop_once(self.session_factory), job_id)

        job = self.get_job(job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error_code, "unknown-job-type")

    def test_existing_running_job_blocks_another_claim(self) -> None:
        self.add_job(status="running", heartbeat_at=datetime.now(timezone.utc))
        pending_job_id = self.add_job(status="pending")

        self.assertIsNone(worker_loop_once(self.session_factory))
        self.assertEqual(self.get_job(pending_job_id).status, "pending")

    def test_environment_check_with_zero_stores_succeeds(self) -> None:
        job_id = self.add_job()
        with (
            patch("lib.zclaw_cli.resolve_ziniao_cli_command", return_value=["node"]),
            patch("lib.zclaw.list_running_stores", return_value=[]),
            patch("lib.app_config.resolve_config_path", return_value=None),
            patch("lib.zclaw.probe_store_page") as probe_store_page,
            patch("lib.zclaw.open_store") as open_store,
        ):
            self.assertEqual(worker_loop_once(self.session_factory), job_id)

        job = self.get_job(job_id)
        self.assertEqual(job.status, "succeeded")
        self.assertIn("running-not-unique", job.result_summary)
        probe_store_page.assert_not_called()
        open_store.assert_not_called()

    def test_environment_check_with_one_store_probes_read_only(self) -> None:
        job_id = self.add_job()
        store = {"storeId": "store-custom", "storeName": "Custom"}
        with (
            patch("lib.zclaw_cli.resolve_ziniao_cli_command", return_value=["node"]),
            patch("lib.zclaw.list_running_stores", return_value=[store]),
            patch("lib.app_config.resolve_config_path", return_value=Path("config.toml")),
            patch("lib.zclaw.probe_store_page", return_value={"ready": "complete"}) as probe,
            patch("lib.zclaw.open_store") as open_store,
        ):
            worker_loop_once(self.session_factory)

        job = self.get_job(job_id)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(json.loads(job.result_summary)["probe"], "ready")
        probe.assert_called_once_with("store-custom")
        open_store.assert_not_called()

    def test_cli_missing_fails_job(self) -> None:
        job_id = self.add_job()
        with patch(
            "lib.zclaw_cli.resolve_ziniao_cli_command",
            side_effect=RuntimeError("missing"),
        ):
            worker_loop_once(self.session_factory)
        job = self.get_job(job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error_code, "cli-not-found")

    def test_cancelled_pending_job_is_never_claimed(self) -> None:
        cancelled_job_id = self.add_job(status="cancelled")
        self.assertIsNone(worker_loop_once(self.session_factory))
        self.assertEqual(self.get_job(cancelled_job_id).status, "cancelled")


class JobProgressTests(JobTestCase):
    def test_list_events_after_sequence(self) -> None:
        job_id = self.add_job()
        append_event(
            self.session_factory,
            job_id,
            level="info",
            event_type="job.started",
            message="first",
        )
        append_event(
            self.session_factory,
            job_id,
            level="info",
            event_type="job.progress",
            message="second",
        )
        append_event(
            self.session_factory,
            job_id,
            level="info",
            event_type="job.completed",
            message="third",
        )

        events = list_events(self.session_factory, job_id, after=1)

        self.assertEqual([event["sequence"] for event in events], [2, 3])


class JobApiTests(JobTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = create_app(runtime_directory=self.root / "runtime", port=8765)
        self.app.state.session_factory = self.session_factory
        install_ziniao_busy_guard(self.app, self.session_factory)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")
        token = self.app.state.session_manager.issue_bootstrap_token()
        response = self.client.get(
            f"/bootstrap?token={token}",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        session = self.app.state.session_manager.read_session(
            self.client.cookies.get("zn_assistant_session")
        )
        self.csrf_token = session["csrf"]

    def tearDown(self) -> None:
        self.client.close()
        super().tearDown()

    def post(self, path: str):
        return self.client.post(
            path,
            headers={
                "Origin": "http://127.0.0.1:8765",
                "X-CSRF-Token": self.csrf_token,
            },
        )

    def test_duplicate_environment_check_returns_same_job(self) -> None:
        first = self.post("/api/jobs/environment-check")
        second = self.post("/api/jobs/environment-check")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertTrue(second.json()["deduplicated"])

    def test_post_without_csrf_is_rejected(self) -> None:
        response = self.client.post(
            "/api/jobs/environment-check",
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        self.assertEqual(response.status_code, 403)

    def test_cancel_pending_prevents_worker_execution(self) -> None:
        job_id = self.post("/api/jobs/environment-check").json()["job_id"]
        response = self.post(f"/api/jobs/{job_id}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_job(job_id).status, "cancelled")
        self.assertIsNone(worker_loop_once(self.session_factory))

    def test_finished_job_cannot_be_cancelled(self) -> None:
        job_id = self.add_job(status="succeeded")
        self.assertEqual(self.post(f"/api/jobs/{job_id}/cancel").status_code, 409)

    def test_running_job_sets_cooperative_cancellation_flag(self) -> None:
        job_id = self.add_job(status="running")
        try:
            response = self.post(f"/api/jobs/{job_id}/cancel")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "cancellation-requested")
            self.assertTrue(is_cancellation_requested(job_id))
        finally:
            clear_cancellation(job_id)

    def test_job_detail_has_fixed_public_shape(self) -> None:
        job_id = self.add_job()
        payload = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(
            set(payload),
            {
                "id", "job_type", "status", "store_id",
                "progress_current", "progress_total", "progress_message",
                "error_code", "error_summary", "result_summary",
                "created_at", "finished_at",
            },
        )

    def test_stores_use_cache_while_ziniao_job_is_running(self) -> None:
        with self.session_factory() as session:
            session.add(
                Store(
                    ziniao_store_id="store-cached",
                    store_name="Cached",
                )
            )
            session.add(
                Job(
                    job_type="environment_check",
                    status="running",
                    requested_by="test",
                )
            )
            session.commit()
        with patch("lib.zclaw.list_running_stores") as list_running_stores:
            response = self.client.get("/api/stores")
        self.assertEqual(response.json()["error"], "ziniao-busy")
        self.assertEqual(response.json()["stores"][0]["storeId"], "store-cached")
        list_running_stores.assert_not_called()

    def test_sse_is_session_protected_and_hides_payload_summary(self) -> None:
        job_id = self.add_job(status="succeeded")
        append_event(
            self.session_factory,
            job_id,
            level="info",
            event_type="job.started",
            message="safe-start",
            payload_summary="access_token=must-not-stream",
        )
        append_event(
            self.session_factory,
            job_id,
            level="info",
            event_type="job.completed",
            message="safe-finish",
        )

        with TestClient(
            self.app,
            base_url="http://127.0.0.1:8765",
        ) as anonymous_client:
            self.assertEqual(
                anonymous_client.get(f"/api/jobs/{job_id}/stream").status_code,
                401,
            )

        response = self.client.get(f"/api/jobs/{job_id}/stream?after=0")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.text.count("data:"), 2)
        self.assertIn('"sequence": 1', response.text)
        self.assertIn('"sequence": 2', response.text)
        self.assertNotIn("must-not-stream", response.text)


if __name__ == "__main__":
    unittest.main()
