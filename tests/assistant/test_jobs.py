from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import assistant.api.jobs as jobs_api
import assistant.jobs.worker as worker_module
from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import Base, Job, Store
from assistant.jobs.locks import (
    clear_cancellation,
    install_ziniao_busy_guard,
    is_cancellation_requested,
    request_cancellation,
)
from assistant.jobs.progress import append_event, list_events
from assistant.jobs.worker import (
    _heartbeat_loop,
    _run_worker_loop,
    mark_stale_jobs_interrupted,
    start_worker,
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


class AdvancingStopEvent:
    """Advance a virtual clock whenever the worker would poll."""

    def __init__(self) -> None:
        self.current_time = 0.0
        self.stop_requested = False

    def is_set(self) -> bool:
        return self.stop_requested

    def set(self) -> None:
        self.stop_requested = True

    def wait(self, timeout: float | None = None) -> bool:
        self.current_time += worker_module.STALE_REAPER_INTERVAL_SECONDS + 1
        return self.stop_requested


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

    def test_runtime_reaper_unblocks_pending_job_after_stale_orphan(self) -> None:
        orphan_job_id = self.add_job(
            status="running",
            heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        pending_job_id = self.add_job(status="pending")

        self.assertEqual(mark_stale_jobs_interrupted(self.session_factory), 0)
        self.assertIsNone(worker_loop_once(self.session_factory))
        self.assertEqual(self.get_job(orphan_job_id).status, "running")
        self.assertEqual(self.get_job(pending_job_id).status, "pending")

        stop_event = AdvancingStopEvent()
        virtual_start_time = datetime.now(timezone.utc)
        original_worker_loop_once = worker_module.worker_loop_once
        claimed_job_ids: list[str] = []

        def complete_job(claimed_job_id: str, session_factory) -> str:
            return "completed"

        def run_worker_iteration(session_factory):
            claimed_job_id = original_worker_loop_once(session_factory)
            if claimed_job_id is not None:
                claimed_job_ids.append(claimed_job_id)
                stop_event.set()
            return claimed_job_id

        with (
            patch.object(
                worker_module,
                "utc_now",
                side_effect=lambda: virtual_start_time
                + timedelta(seconds=stop_event.current_time),
            ),
            patch.object(worker_module, "get_handler", return_value=complete_job),
            patch.object(
                worker_module,
                "worker_loop_once",
                side_effect=run_worker_iteration,
            ),
        ):
            _run_worker_loop(
                self.session_factory,
                stop_event,
                monotonic=lambda: stop_event.current_time,
            )

        self.assertEqual(self.get_job(orphan_job_id).status, "interrupted")
        self.assertEqual(self.get_job(pending_job_id).status, "succeeded")
        self.assertEqual(claimed_job_ids, [pending_job_id])

    def test_claim_event_failure_clears_active_worker_state(self) -> None:
        job_id = self.add_job()
        request_cancellation(job_id)
        try:
            with patch.object(
                worker_module,
                "append_event",
                side_effect=RuntimeError("event failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "event failure"):
                    worker_loop_once(self.session_factory)

            self.assertIsNone(worker_module._active_job_id)
            self.assertFalse(is_cancellation_requested(job_id))
            self.assertEqual(self.get_job(job_id).status, "running")
        finally:
            clear_cancellation(job_id)

    def test_worker_iteration_error_does_not_kill_daemon(self) -> None:
        first_iteration_started = threading.Event()
        second_iteration_started = threading.Event()
        iteration_count = 0

        def run_worker_iteration(session_factory):
            nonlocal iteration_count
            iteration_count += 1
            if iteration_count == 1:
                first_iteration_started.set()
                raise RuntimeError("transient worker failure")
            second_iteration_started.set()
            return None

        with (
            patch.object(
                worker_module,
                "worker_loop_once",
                side_effect=run_worker_iteration,
            ),
            patch.object(worker_module.logger, "exception") as log_exception,
        ):
            worker_controller = start_worker(self.session_factory)
            try:
                self.assertTrue(first_iteration_started.wait(timeout=1.0))
                self.assertTrue(worker_controller.thread.is_alive())
                self.assertTrue(second_iteration_started.wait(timeout=1.0))
                self.assertTrue(worker_controller.thread.is_alive())
            finally:
                worker_controller.stop(timeout=1.0)

        self.assertFalse(worker_controller.thread.is_alive())
        self.assertGreaterEqual(iteration_count, 2)
        log_exception.assert_any_call("job worker iteration failed")

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

    def test_running_job_heartbeat_refreshes_while_handler_can_block(self) -> None:
        old_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=30)
        stored_old_heartbeat = old_heartbeat.replace(tzinfo=None)
        job_id = self.add_job(status="running", heartbeat_at=old_heartbeat)
        stop_event = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(self.session_factory, job_id, stop_event),
            kwargs={"interval": 0.01},
        )
        heartbeat_thread.start()
        try:
            heartbeat_deadline = time.monotonic() + 1.0
            refreshed_heartbeat: datetime | None = None
            while time.monotonic() < heartbeat_deadline:
                current_heartbeat = self.get_job(job_id).heartbeat_at
                if (
                    current_heartbeat is not None
                    and current_heartbeat > stored_old_heartbeat
                ):
                    refreshed_heartbeat = current_heartbeat
                    break
                time.sleep(0.01)

            self.assertIsNotNone(
                refreshed_heartbeat,
                "Job heartbeat did not advance within one second.",
            )
        finally:
            stop_event.set()
            heartbeat_thread.join(timeout=1.0)

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

    def test_daily_refresh_runs_logistics_then_followups(self) -> None:
        job_id = self.add_job(job_type="daily_refresh")
        store = {"storeId": "store-custom", "storeName": "Custom"}
        with (
            patch(
                "assistant.jobs.handlers.daily_refresh.StoreService.resolve_unique_running_store",
                return_value={"ok": True, "store": store},
            ),
            patch(
                "assistant.jobs.handlers.daily_refresh.ShipmentService.synchronize_shipments",
                return_value={"synchronized": 4, "changed": 2, "processing": 3},
            ) as synchronize_shipments,
            patch(
                "assistant.jobs.handlers.daily_refresh.FollowupService.generate",
                return_value={"created": 2},
            ) as generate_followups,
        ):
            self.assertEqual(worker_loop_once(self.session_factory), job_id)

        job = self.get_job(job_id)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(
            json.loads(job.result_summary),
            {
                "shipment": {"synchronized": 4, "changed": 2, "processing": 3},
                "followup": {"created": 2},
            },
        )
        synchronize_shipments.assert_called_once()
        generate_followups.assert_called_once_with()
        messages = [
            event["message"]
            for event in list_events(self.session_factory, job_id)
        ]
        self.assertIn("物流和到货状态已更新", messages)
        self.assertIn("今日更新完成", messages)

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

    def test_shipment_sync_honours_running_cancellation_checkpoint(self) -> None:
        job_id = self.add_job(job_type="shipment_sync")
        request_cancellation(job_id)
        try:
            with (
                patch("assistant.services.store_service.StoreService.resolve_unique_running_store",
                      return_value={"ok": True, "store": {"storeId": "cancelled-store", "storeName": "C"}}),
                patch("assistant.services.shipment_service.ShipmentService.synchronize_shipments") as sync,
            ):
                worker_loop_once(self.session_factory)
            sync.assert_not_called()
        finally:
            clear_cancellation(job_id)
        job = self.get_job(job_id)
        self.assertEqual(job.status, "cancelled")

    def test_daily_refresh_honours_running_cancellation_checkpoint(self) -> None:
        job_id = self.add_job(job_type="daily_refresh")
        request_cancellation(job_id)
        try:
            with (
                patch("assistant.services.store_service.StoreService.resolve_unique_running_store",
                      return_value={"ok": True, "store": {"storeId": "cancelled-store", "storeName": "C"}}),
                patch("assistant.services.shipment_service.ShipmentService.synchronize_shipments") as sync,
            ):
                worker_loop_once(self.session_factory)
            sync.assert_not_called()
        finally:
            clear_cancellation(job_id)
        job = self.get_job(job_id)
        self.assertEqual(job.status, "cancelled")


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
        self.app = create_app(port=8765)
        self.app.state.session_factory = self.session_factory
        install_ziniao_busy_guard(self.app, self.session_factory)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")

    def tearDown(self) -> None:
        self.client.close()
        super().tearDown()

    def post(self, path: str):
        return self.client.post(
            path,
            headers={"Origin": "http://127.0.0.1:8765"},
        )

    def test_duplicate_environment_check_returns_same_job(self) -> None:
        first = self.post("/api/jobs/environment-check")
        second = self.post("/api/jobs/environment-check")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertTrue(second.json()["deduplicated"])

    def test_duplicate_daily_refresh_returns_same_job(self) -> None:
        first = self.post("/api/jobs/daily-refresh")
        second = self.post("/api/jobs/daily-refresh")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertTrue(second.json()["deduplicated"])

    def test_post_without_origin_is_rejected(self) -> None:
        response = self.client.post("/api/jobs/environment-check")
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

    def test_pending_cancel_cannot_overwrite_worker_claim(self) -> None:
        job_id = self.add_job()
        cancel_update_barrier = threading.Barrier(2)
        cancel_update_reached = threading.Event()
        worker_claimed = threading.Event()
        handler_started = threading.Event()
        handler_release = threading.Event()
        handler_observed_cancellation: list[bool] = []
        cancellation_result: dict[str, dict] = {}
        cancellation_errors: list[Exception] = []
        worker_result: dict[str, str | None] = {}
        worker_errors: list[Exception] = []
        cancellation_request = SimpleNamespace(app=self.app)
        original_update = jobs_api.update
        original_claim_next_job = worker_module._claim_next_job

        def gated_update(model):
            update_statement = original_update(model)
            cancel_update_reached.set()
            cancel_update_barrier.wait(timeout=1.0)
            return update_statement

        def claim_and_release(session_factory):
            claimed_job_id = original_claim_next_job(session_factory)
            if claimed_job_id == job_id:
                worker_claimed.set()
                cancel_update_barrier.wait(timeout=1.0)
            return claimed_job_id

        def continue_at_safe_checkpoint(
            claimed_job_id: str,
            session_factory,
        ) -> str:
            handler_started.set()
            if not handler_release.wait(timeout=1.0):
                raise AssertionError("handler release timed out")
            handler_observed_cancellation.append(
                is_cancellation_requested(claimed_job_id)
            )
            return "safe-checkpoint"

        def run_cancellation() -> None:
            try:
                cancellation_result["response"] = jobs_api.cancel_job(
                    job_id,
                    cancellation_request,
                )
            except Exception as error:
                cancellation_errors.append(error)

        def run_worker() -> None:
            try:
                worker_result["job_id"] = worker_loop_once(self.session_factory)
            except Exception as error:
                worker_errors.append(error)

        cancellation_thread = threading.Thread(
            target=run_cancellation,
            name="test-cancellation-request",
            daemon=True,
        )
        worker_thread = threading.Thread(
            target=run_worker,
            name="test-worker-claim",
            daemon=True,
        )

        with (
            patch.object(jobs_api, "update", side_effect=gated_update),
            patch.object(
                worker_module,
                "_claim_next_job",
                side_effect=claim_and_release,
            ),
            patch.object(
                worker_module,
                "get_handler",
                return_value=continue_at_safe_checkpoint,
            ),
        ):
            cancellation_thread.start()
            self.assertTrue(cancel_update_reached.wait(timeout=1.0))
            worker_thread.start()
            self.assertTrue(worker_claimed.wait(timeout=1.0))
            self.assertTrue(handler_started.wait(timeout=1.0))
            cancellation_thread.join(timeout=1.0)
            self.assertFalse(cancellation_thread.is_alive())
            self.assertEqual(cancellation_errors, [])
            self.assertEqual(
                cancellation_result["response"],
                {"job_id": job_id, "status": "cancellation-requested"},
            )
            self.assertEqual(self.get_job(job_id).status, "running")
            self.assertTrue(is_cancellation_requested(job_id))
            handler_release.set()
            worker_thread.join(timeout=1.0)

        self.assertFalse(worker_thread.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertEqual(worker_result["job_id"], job_id)
        self.assertEqual(handler_observed_cancellation, [True])
        self.assertEqual(self.get_job(job_id).status, "succeeded")
        self.assertFalse(is_cancellation_requested(job_id))

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

    def test_diagnostics_use_cache_while_ziniao_job_is_running(self) -> None:
        self._add_cached_store_and_running_job()
        with (
            patch("lib.zclaw.list_running_stores") as list_running_stores,
            patch("lib.zclaw_cli.resolve_ziniao_cli_command", return_value=["node"]),
            patch("lib.app_config.resolve_config_path", return_value=None),
        ):
            response = self.client.get("/diagnostics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ZINIAO_BUSY", response.text)
        self.assertIn("store-cached", response.text)
        list_running_stores.assert_not_called()

    def test_home_uses_cache_while_ziniao_job_is_running(self) -> None:
        self._add_cached_store_and_running_job()
        with patch("lib.zclaw.list_running_stores") as list_running_stores:
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ziniao-busy", response.text)
        list_running_stores.assert_not_called()

    def _add_cached_store_and_running_job(self) -> None:
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

    def test_sse_stream_hides_payload_summary(self) -> None:
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
                anonymous_client.get(f"/api/jobs/{job_id}/stream?after=0").status_code,
                200,
            )
            self.assertNotIn("must-not-stream", anonymous_client.get(f"/api/jobs/{job_id}/stream?after=0").text)

        response = self.client.get(f"/api/jobs/{job_id}/stream?after=0")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.text.count("data:"), 2)
        self.assertIn('"sequence": 1', response.text)
        self.assertIn('"sequence": 2', response.text)
        self.assertNotIn("must-not-stream", response.text)


if __name__ == "__main__":
    unittest.main()
