"""Offline admission, immutable evidence, one-shot claims and recovery tests."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, Job, TargetCleanupBatch, TargetCleanupItem
from assistant.services import target_cleanup_service as service
from assistant.services.release_lifecycle import LifecycleCoordinator, ServiceStopping
from lib import target_invitation_dom as invitations
from lib import target_plan_cleanup as core
from tests.test_target_invitation_dom import make_page, make_row

PAGE_STATE = {
    "ok": True, "href": "https://seller.us.tiktokshopglobalselling.com/homepage",
    "page_type": "seller-center", "shop_id": "shop-1", "shop_region": "US",
}


class TargetCleanupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_database_engine(self.root / "assistant.sqlite3")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.other_session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.paths_patcher = patch.object(service, "ensure_user_dirs", return_value=self.root)
        self.paths_patcher.start()
        self.running_patcher = patch("lib.zclaw.list_running_stores", return_value=[{"storeId": "store-1", "storeName": "Bound store"}])
        self.running_mock = self.running_patcher.start()
        self.page_patcher = patch("lib.zclaw.zclaw_exec", return_value=dict(PAGE_STATE))
        self.page_mock = self.page_patcher.start()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.paths_patcher.stop)
        self.addCleanup(self.running_patcher.stop)
        self.addCleanup(self.page_patcher.stop)

    def assert_code(self, code, function, *arguments, **keywords):
        with self.assertRaises(service.TargetCleanupServiceError) as captured:
            function(*arguments, **keywords)
        self.assertEqual(captured.exception.code, code)

    def _job_status(self, job_id: str, status: str) -> None:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            job.status = status
            job.result_summary = "worker-overwrote-the-request"
            session.commit()

    def _preview(self, *, key="preview-key", months=4, identifiers=("invite-1", "invite-2"),
                 complete=True, started_at=None, finished_at=None):
        created = service.create_preview(self.session_factory, months, key)
        self._job_status(created["job_id"], "running")
        if started_at is not None:
            frozen = core.freeze_rule(batch_id=created["batch_id"], store_id="store-1", shop_id="shop-1", months=months, started_at=started_at)
            with patch.object(core, "freeze_rule", return_value=frozen):
                request = service.begin_script(self.session_factory, created["batch_id"], created["job_id"], "preview")
        else:
            request = service.begin_script(self.session_factory, created["batch_id"], created["job_id"], "preview")
        cutoff = datetime.fromisoformat(request["frozen"]["cutoff"]).date()
        rows = [{
            "invitation_id": identifier, "name": "Invitation " + identifier,
            "last_modified": (cutoff - timedelta(days=1)).isoformat(),
            "accepted_count": 3, "promoted_count": 1, "invited_count": 5,
        } for identifier in identifiers]
        snapshot = core.create_snapshot(request["frozen"], {
            "rows": rows, "scan_complete": complete,
            "stop_reason": ("first-page" if identifiers else "empty-list") if complete else "page-limit",
            "pages_scanned": 1,
        }, finished_at=finished_at)
        paths = service.artifact_paths(created["batch_id"])
        core.write_csv_report(paths["scan_csv"], snapshot["rows"])
        core.write_csv_report(paths["candidates_csv"], [row for row in snapshot["rows"] if row["eligible"]])
        core.atomic_write_json(paths["snapshot"], snapshot, immutable=True)
        service.finish_preview(self.session_factory, created["batch_id"], snapshot)
        self._job_status(created["job_id"], "succeeded")
        return created, snapshot

    def _execution(self, preview, *, key="execute-key", begin=True):
        created = service.create_execution(self.session_factory, preview["batch_id"], " y ", key)
        if begin:
            self._job_status(created["job_id"], "running")
            service.begin_script(self.session_factory, preview["batch_id"], created["job_id"], "execute")
        return created

    def _results(self, batch_id: str, statuses: list[str]):
        payload = service.batch_payload(self.session_factory, batch_id)
        timestamp = datetime.now(timezone.utc).isoformat()
        results = []
        for item, status in zip(payload["items"], statuses):
            results.append({
                **item, "status": status, "action": "cancel-submitted" if status == "submitted" else status,
                "summary": status, "attempted_at": timestamp if status in {"attempting", "submitted", "uncertain"} else "",
                "returned_at": timestamp if status in {"submitted", "skipped", "failed"} else "",
            })
        return {"schema_version": 1, "batch_id": batch_id, "snapshot_sha256": payload["snapshot_sha256"], "items": results}

    def _write_results(self, batch_id: str, statuses: list[str]):
        results = self._results(batch_id, statuses)
        core.atomic_write_json(service.artifact_paths(batch_id)["results_json"], results)
        return results

    def test_preview_job_and_request_committed_together_and_freeze_at_worker_start(self):
        created = service.create_preview(self.session_factory, 2, "request-key")
        with self.other_session_factory() as session:
            batch = session.get(TargetCleanupBatch, created["batch_id"])
            self.assertEqual(batch.frozen_json, "")
            self.assertEqual(batch.preview_status, "queued")
            self.assertEqual(session.get(Job, created["job_id"]).job_type, service.PREVIEW_JOB_TYPE)
            self.assertTrue(batch.snapshot_path.startswith("exports/target_cleanup/"))
        self._job_status(created["job_id"], "running")
        request = service.begin_script(self.other_session_factory, created["batch_id"], created["job_id"], "preview")
        self.assertEqual(request["frozen"]["run_date"], datetime.now().astimezone().date().isoformat())
        self.assertEqual(request["frozen"]["months"], 2)
        with self.session_factory() as session:
            self.assertIsNotNone(session.get(TargetCleanupBatch, created["batch_id"]).preview_started_at.tzinfo)

    def test_same_key_returns_completed_request_before_busy_or_probe(self):
        preview, _snapshot = self._preview()
        with self.session_factory() as session:
            session.add(Job(id="busy", job_type="operator_tracking", status="pending"))
            session.commit()
        self.running_mock.reset_mock()
        self.page_mock.reset_mock()
        duplicate = service.create_preview(self.session_factory, 4, "preview-key")
        self.assertEqual(duplicate, {**preview, "deduplicated": True})
        self.running_mock.assert_not_called()
        self.page_mock.assert_not_called()
        self.assert_code("idempotency-conflict", service.create_preview, self.session_factory, 2, "preview-key")

    def test_all_page_job_types_block_pending_and_running_before_identity(self):
        page_types = set(service.blocking_job_types()) | {service.PREVIEW_JOB_TYPE, service.EXECUTE_JOB_TYPE}
        for job_type in page_types:
            for status in ("pending", "running"):
                with self.subTest(job_type=job_type, status=status):
                    with self.session_factory() as session:
                        session.add(Job(id="busy", job_type=job_type, status=status))
                        session.commit()
                    self.assert_code("store-busy", service.create_preview, self.session_factory, 4, "new-key")
                    self.running_mock.assert_not_called()
                    self.page_mock.assert_not_called()
                    with self.session_factory() as session:
                        session.delete(session.get(Job, "busy"))
                        session.commit()

    def test_months_keys_and_unknown_batch_are_strict(self):
        for months in (True, "2", 0, 3, 4.0):
            self.assert_code("invalid-months", service.create_preview, self.session_factory, months, "key")
        for key in ("", " " * 5, "x" * 129, None):
            self.assert_code("invalid-idempotency-key", service.create_preview, self.session_factory, 4, key)
        self.assert_code("invalid-batch-id", service.batch_payload, self.session_factory, "../snapshot")
        self.running_mock.assert_not_called()

    def test_identity_empty_multistore_and_unknown_context_fail_closed(self):
        for stores in ([], [{"storeId": "one"}, {"storeId": "two"}], [{}], None):
            with self.subTest(stores=stores):
                self.running_mock.return_value = stores
                self.assert_code("running-not-unique" if stores != [{}] else "invalid-store-identity", service.create_preview, self.session_factory, 4, "key")
        self.page_mock.assert_not_called()
        self.running_mock.return_value = [{"storeId": "store-1"}]
        for state in ({**PAGE_STATE, "shop_id": ""}, {**PAGE_STATE, "page_type": "unknown"},
                      {**PAGE_STATE, "page_type": "login"}, {**PAGE_STATE, "href": "about:blank"}, {}):
            with self.subTest(state=state):
                self.page_mock.return_value = state
                with self.assertRaises(service.TargetCleanupServiceError):
                    service.create_preview(self.session_factory, 4, "key")
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 0)

    def test_two_sessions_same_preview_request_admit_once(self):
        barrier = threading.Barrier(2)
        def create_request(factory):
            barrier.wait(timeout=5)
            return service.create_preview(factory, 4, "same-key")
        with ThreadPoolExecutor(max_workers=2) as executor:
            requests = list(executor.map(create_request, [self.session_factory, self.other_session_factory]))
        self.assertEqual(requests[0]["batch_id"], requests[1]["batch_id"])
        self.assertEqual(sum(not item["deduplicated"] for item in requests), 1)
        self.running_mock.assert_called_once()
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)

    def test_two_different_requests_cannot_both_admit(self):
        barrier = threading.Barrier(2)
        def create_request(arguments):
            factory, months, key = arguments
            barrier.wait(timeout=5)
            try:
                return service.create_preview(factory, months, key)
            except service.TargetCleanupServiceError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(create_request, [
                (self.session_factory, 2, "first"), (self.other_session_factory, 4, "second"),
            ]))
        self.assertEqual(sum(isinstance(result, dict) for result in outcomes), 1)
        self.assertIn("store-busy", outcomes)
        self.running_mock.assert_called_once()

    def test_release_admission_stopping_creates_nothing(self):
        coordinator = LifecycleCoordinator()
        coordinator.stopping = True
        self.session_factory.release_coordinator = coordinator
        with self.assertRaises(ServiceStopping):
            service.create_preview(self.session_factory, 4, "key")
        self.running_mock.assert_not_called()

    def test_empty_and_incomplete_previews_never_execute(self):
        for identifiers, complete in (((), True), (("invite-1",), False)):
            with self.subTest(identifiers=identifiers, complete=complete):
                preview, _snapshot = self._preview(key=str(complete), identifiers=identifiers, complete=complete)
                self.assert_code("empty-preview" if complete else "preview-not-completed", service.create_execution,
                                 self.session_factory, preview["batch_id"], "y", "execution-" + str(complete))
                payload = service.batch_payload(self.session_factory, preview["batch_id"])
                self.assertFalse(payload["can_execute"])
                self.assertEqual(payload["preview_status"], "completed" if complete else "incomplete")

    def test_actual_unknown_total_scan_retains_reports_without_execution_admission(self):
        preview = service.create_preview(self.session_factory, 4, "unknown-total-preview")
        self._job_status(preview["job_id"], "running")
        request = service.begin_script(self.session_factory, preview["batch_id"], preview["job_id"], "preview")
        cutoff = datetime.fromisoformat(request["frozen"]["cutoff"]).date()
        href = "https://affiliate.tiktokshopglobalselling.com/affiliate/collaboration/target-invitation?shop_id=shop-1&shop_region=US"
        pages = [
            make_page(2, [make_row(f"old-{row_index}", (cutoff - timedelta(days=1)).isoformat())
                          for row_index in range(20)], total="", href=href),
            make_page(1, [make_row(f"boundary-{row_index}", cutoff.isoformat()) for row_index in range(20)],
                      total="", href=href, next_disabled=False),
        ]
        with patch.object(invitations, "restore_ongoing_list", return_value={"nav": {"ok": True}}), \
                patch.object(invitations, "extract_page", side_effect=pages) as extract, \
                patch.object(invitations, "zclaw_exec", return_value={"ok": True}) as transport, \
                patch.object(invitations.time, "sleep"), \
                patch.object(invitations, "cancel_invitation_by_id") as cancel:
            scan = invitations.scan_older_invitations(request["store_id"], cutoff=cutoff)
            snapshot = core.create_snapshot(request["frozen"], scan)
            digest = core.persist_preview(snapshot, Path(request["paths"]["snapshot"]).parent)
            service.finish_preview(self.session_factory, preview["batch_id"], snapshot)
            self._job_status(preview["job_id"], "succeeded")
            self.running_mock.reset_mock()
            self.page_mock.reset_mock()
            payload = service.batch_payload(self.session_factory, preview["batch_id"])
            self.assertFalse(payload["scan_complete"])
            self.assertFalse(payload["can_execute"])
            self.assertEqual(payload["preview_status"], "incomplete")
            self.assertEqual(payload["stop_reason"], "page-total-unverified")
            self.assertEqual(payload["scan_count"], 20)
            self.assertEqual(payload["candidate_count"], 20)
            self.assertEqual(payload["snapshot_sha256"], digest)
            self.assert_code("preview-not-completed", service.create_execution, self.session_factory,
                             preview["batch_id"], "y", "unknown-total-execute")
            paths = service.artifact_paths(preview["batch_id"])
            for report_name in ("scan_csv", "candidates_csv"):
                self.assertIn(report_name, payload["downloads"])
                self.assertIn(b"old-19", paths[report_name].read_bytes())
            self.assertFalse(paths["execute_envelope"].exists())
            self.assertFalse(paths["backup_json"].exists())
            extract.assert_called_once_with("store-1")
            transport.assert_called_once_with("store-1", invitations.PREPARE_LIST_JS)
            cancel.assert_not_called()
            self.running_mock.assert_not_called()
            self.page_mock.assert_not_called()
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(Job).where(
                Job.job_type == service.EXECUTE_JOB_TYPE)), 0)
            self.assertIsNone(session.get(TargetCleanupBatch, preview["batch_id"]).execute_job_id)

    def test_fresh_v1_preview_is_rejected_without_resigning_or_platform_probe(self):
        with patch.object(core, "IMPLEMENTATION_VERSION", "target-cleanup-v1"):
            preview, snapshot = self._preview()
        paths = service.artifact_paths(preview["batch_id"])
        original_files = {path: path.read_bytes() for path in paths.values() if path.exists()}
        self.assertEqual(core.payload_digest(snapshot), service.batch_payload(
            self.session_factory, preview["batch_id"])["snapshot_sha256"])
        self.assertGreater(core.parse_timestamp(snapshot["expires_at"]), datetime.now(timezone.utc))
        self.running_mock.reset_mock()
        self.page_mock.reset_mock()
        self.assert_code("invalid-snapshot", service.create_execution, self.session_factory,
                         preview["batch_id"], "y", "new-execute")
        self.assertEqual({path: path.read_bytes() for path in paths.values() if path.exists()}, original_files)
        self.running_mock.assert_not_called()
        self.page_mock.assert_not_called()
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)
            batch = session.get(TargetCleanupBatch, preview["batch_id"])
            self.assertEqual(batch.execute_status, "unclaimed")
            self.assertIsNone(batch.execute_idempotency_key)

    def test_v1_execution_queued_before_upgrade_is_rejected_at_worker_start(self):
        with patch.object(core, "IMPLEMENTATION_VERSION", "target-cleanup-v1"):
            preview, _snapshot = self._preview()
            execution = self._execution(preview, begin=False)
        self._job_status(execution["job_id"], "running")
        self.running_mock.reset_mock()
        self.page_mock.reset_mock()
        self.assert_code("invalid-snapshot", service.begin_script, self.session_factory,
                         preview["batch_id"], execution["job_id"], "execute")
        self.assertEqual(service.batch_payload(self.session_factory, preview["batch_id"])["execute_status"], "queued")
        self.assertFalse(service.artifact_paths(preview["batch_id"])["backup_json"].exists())
        self.running_mock.assert_not_called()
        self.page_mock.assert_not_called()

    def test_confirmation_only_y_and_global_phase_key_conflicts(self):
        preview, _snapshot = self._preview()
        for answer in ("yes", "", "n", True):
            self.assert_code("confirmation-required", service.create_execution, self.session_factory, preview["batch_id"], answer, "key")
        self.assert_code("idempotency-conflict", service.create_execution, self.session_factory, preview["batch_id"], "y", "preview-key")
        created = self._execution(preview, begin=False)
        self.running_mock.reset_mock()
        self.page_mock.reset_mock()
        duplicate = service.create_execution(self.session_factory, preview["batch_id"], "Y", "execute-key")
        self.assertEqual(duplicate, {**created, "deduplicated": True})
        self.assert_code("batch-already-consumed", service.create_execution, self.session_factory, preview["batch_id"], "y", "another-key")
        self.assert_code("idempotency-conflict", service.create_preview, self.session_factory, 4, "execute-key")
        self.running_mock.assert_not_called()
        self.page_mock.assert_not_called()

    def test_two_sessions_execution_admit_once_with_server_envelope(self):
        preview, _snapshot = self._preview()
        barrier = threading.Barrier(2)
        def create_request(factory):
            barrier.wait(timeout=5)
            return service.create_execution(factory, preview["batch_id"], "y", "same-execute-key")
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(create_request, [self.session_factory, self.other_session_factory]))
        self.assertEqual(outcomes[0]["job_id"], outcomes[1]["job_id"])
        self.assertEqual(sum(not result["deduplicated"] for result in outcomes), 1)
        envelope = json.loads(service.artifact_paths(preview["batch_id"])["execute_envelope"].read_bytes())
        self.assertEqual(set(envelope), service.ENVELOPE_FIELDS)
        self.assertEqual(envelope["job_id"], outcomes[0]["job_id"])
        self.assertEqual(envelope["confirmation"], "y")
        self.assertEqual(service.batch_payload(self.session_factory, preview["batch_id"])["total"], 2)

    def test_execution_checks_identity_before_enqueue_and_at_actual_start(self):
        preview, _snapshot = self._preview()
        self.page_mock.return_value = {**PAGE_STATE, "shop_id": "switched"}
        self.assert_code("store-identity-mismatch", service.create_execution, self.session_factory, preview["batch_id"], "y", "key")
        self.page_mock.return_value = dict(PAGE_STATE)
        created = self._execution(preview, begin=False)
        self._job_status(created["job_id"], "running")
        self.running_mock.return_value = [{"storeId": "switched"}]
        self.assert_code("store-identity-mismatch", service.begin_script, self.session_factory, preview["batch_id"], created["job_id"], "execute")
        self.assertEqual(service.batch_payload(self.session_factory, preview["batch_id"])["execute_status"], "queued")

    def test_claim_requires_exact_running_job_and_cannot_claim_twice(self):
        preview, _snapshot = self._preview()
        created = self._execution(preview, begin=False)
        self.assert_code("job-claim-required", service.begin_script, self.session_factory, preview["batch_id"], created["job_id"], "execute")
        self._job_status(created["job_id"], "running")
        self.assert_code("job-claim-required", service.begin_script, self.session_factory, preview["batch_id"], preview["job_id"], "execute")
        request = service.begin_script(self.session_factory, preview["batch_id"], created["job_id"], "execute")
        self.assertEqual(request["snapshot"]["candidate_ids"], ["invite-1", "invite-2"])
        self.assert_code("batch-already-claimed", service.begin_script, self.other_session_factory, preview["batch_id"], created["job_id"], "execute")

    def test_modified_snapshot_and_database_evidence_are_rejected(self):
        preview, snapshot = self._preview()
        path = service.artifact_paths(preview["batch_id"])["snapshot"]
        original = path.read_bytes()
        modified = {**snapshot, "store_id": "other-store"}
        core.atomic_write_json(path, modified)
        self.assert_code("snapshot-hash-mismatch", service.create_execution, self.session_factory, preview["batch_id"], "y", "key")
        core.atomic_write_bytes(path, original)
        with self.session_factory() as session:
            item = session.scalar(select(TargetCleanupItem))
            evidence = json.loads(item.row_json)
            evidence["name"] = "edited"
            item.row_json = core.canonical_json(evidence).decode()
            session.commit()
        self.assert_code("candidate-evidence-mismatch", service.create_execution, self.session_factory, preview["batch_id"], "y", "key")

    def test_expiry_and_local_day_are_separate_guards(self):
        now = datetime.now(timezone.utc)
        for suffix, start, finished, expected in (
            ("expired", now - timedelta(hours=1), now - timedelta(minutes=31), "expired-preview"),
            ("day", now - timedelta(days=1), now, "different-local-day"),
        ):
            with self.subTest(expected=expected):
                preview, _snapshot = self._preview(key=suffix, started_at=start, finished_at=finished)
                self.assert_code(expected, service.create_execution, self.session_factory, preview["batch_id"], "y", suffix + "-exec")

    def test_digest_rechecked_at_script_start_and_envelope_cannot_be_edited(self):
        preview, snapshot = self._preview()
        created = self._execution(preview, begin=False)
        self._job_status(created["job_id"], "running")
        paths = service.artifact_paths(preview["batch_id"])
        original = paths["snapshot"].read_bytes()
        core.atomic_write_json(paths["snapshot"], {**snapshot, "shop_id": "changed"})
        self.assert_code("snapshot-hash-mismatch", service.begin_script, self.session_factory, preview["batch_id"], created["job_id"], "execute")
        core.atomic_write_bytes(paths["snapshot"], original)
        core.atomic_write_json(paths["execute_envelope"], {"confirmation": "y"})
        self.assert_code("execution-envelope-mismatch", service.begin_script, self.session_factory, preview["batch_id"], created["job_id"], "execute")

    def test_row_checkpoint_and_results_are_persisted_without_overwriting_evidence(self):
        preview, _snapshot = self._preview()
        self._execution(preview)
        results = self._write_results(preview["batch_id"], ["submitted", "pending"])
        self.assert_code("attempt-checkpoint-required", service.save_item_result, self.session_factory, preview["batch_id"], results["items"][0])
        service.mark_item_attempting(self.session_factory, preview["batch_id"], "invite-1")
        self.assert_code("item-already-consumed", service.mark_item_attempting, self.session_factory, preview["batch_id"], "invite-1")
        service.save_item_result(self.session_factory, preview["batch_id"], results["items"][0])
        tampered = {**results["items"][0], "name": "must-not-overwrite"}
        self.assert_code("result-evidence-mismatch", service.save_item_result, self.session_factory, preview["batch_id"], tampered)
        service.finish_execution(self.session_factory, preview["batch_id"], "completed")
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertEqual(payload["execute_status"], "partial")
        self.assertEqual(payload["counts"]["submitted"], 1)
        self.assertEqual(payload["counts"]["not_processed"], 1)

    def test_all_submitted_is_completed_not_platform_confirmation(self):
        preview, _snapshot = self._preview()
        self._execution(preview)
        results = self._write_results(preview["batch_id"], ["submitted", "submitted"])
        for result in results["items"]:
            service.mark_item_attempting(self.session_factory, preview["batch_id"], result["invitation_id"])
            service.save_item_result(self.session_factory, preview["batch_id"], result)
        service.finish_execution(self.session_factory, preview["batch_id"], "completed")
        self.assertEqual(service.batch_payload(self.session_factory, preview["batch_id"])["execute_status"], "completed")

    def test_abandoned_disk_attempt_before_db_callback_recovers_to_uncertain(self):
        preview, _snapshot = self._preview()
        execution = self._execution(preview)
        self._write_results(preview["batch_id"], ["attempting", "pending"])
        self._job_status(execution["job_id"], "interrupted")
        service.recover_batch(self.other_session_factory, preview["batch_id"])
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertEqual(payload["execute_status"], "needs_review")
        self.assertEqual([item["status"] for item in payload["items"]], ["uncertain", "not_processed"])
        self._write_results(preview["batch_id"], ["submitted", "pending"])
        service.recover_batch(self.session_factory, preview["batch_id"])
        self.assertEqual(service.batch_payload(self.session_factory, preview["batch_id"])["items"][0]["status"], "uncertain")

    def test_missing_or_bad_journal_preserves_db_submitted_and_uncertain(self):
        preview, _snapshot = self._preview()
        execution = self._execution(preview)
        results = self._results(preview["batch_id"], ["submitted", "pending"])
        service.mark_item_attempting(self.session_factory, preview["batch_id"], "invite-1")
        service.save_item_result(self.session_factory, preview["batch_id"], results["items"][0])
        self._job_status(execution["job_id"], "failed")
        service.recover_batch(self.session_factory, preview["batch_id"])
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertEqual([item["status"] for item in payload["items"]], ["submitted", "uncertain"])
        self.assertEqual(payload["error_code"], "result-journal-invalid")

    def test_recovery_rejects_wrong_batch_digest_ids_and_original_row(self):
        for variant in ("batch", "digest", "id", "name", "truncated"):
            with self.subTest(variant=variant):
                preview, _snapshot = self._preview(key=variant, identifiers=(variant + "-first", variant + "-second"))
                execution = self._execution(preview, key=variant + "-execute")
                results = self._results(preview["batch_id"], ["submitted", "pending"])
                if variant == "batch":
                    results["batch_id"] = "other"
                elif variant == "digest":
                    results["snapshot_sha256"] = "wrong"
                elif variant == "truncated":
                    results["items"] = results["items"][:1]
                else:
                    results["items"][0]["invitation_id" if variant == "id" else "name"] = "tampered"
                core.atomic_write_json(service.artifact_paths(preview["batch_id"])["results_json"], results)
                self._job_status(execution["job_id"], "interrupted")
                service.recover_batch(self.session_factory, preview["batch_id"])
                payload = service.batch_payload(self.session_factory, preview["batch_id"])
                self.assertEqual(payload["counts"]["uncertain"], 2)
                self.assertNotEqual(payload["items"][0]["name"], "tampered")

    def test_queued_cancellation_never_creates_uncertainty_and_active_not_recovered(self):
        preview, _snapshot = self._preview()
        execution = self._execution(preview, begin=False)
        service.recover_batch(self.session_factory, preview["batch_id"])
        self.assertEqual(service.batch_payload(self.session_factory, preview["batch_id"])["execute_status"], "queued")
        self._job_status(execution["job_id"], "cancelled")
        service.recover_batch(self.session_factory, preview["batch_id"])
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertEqual(payload["counts"]["uncertain"], 0)
        self.assertEqual(payload["counts"]["not_processed"], 2)
        self.assert_code("batch-already-consumed", service.create_execution, self.session_factory, preview["batch_id"], "y", "fresh-key")

    def test_same_store_historical_side_effect_blocks_new_execution(self):
        for status in ("submitted", "uncertain", "attempting"):
            with self.subTest(status=status):
                first, _snapshot = self._preview(key=status, identifiers=("overlap-" + status,))
                execution = self._execution(first, key=status + "-exec")
                self._write_results(first["batch_id"], [status])
                self._job_status(execution["job_id"], "interrupted")
                second, _snapshot = self._preview(key=status + "-second", identifiers=("overlap-" + status,))
                self.assert_code("historical-write-requires-review", service.create_execution,
                                 self.session_factory, second["batch_id"], "y", status + "-second-exec")
                self.assertEqual(service.batch_payload(self.session_factory, second["batch_id"])["execute_status"], "unclaimed")
                recovered = service.batch_payload(self.session_factory, first["batch_id"])
                self.assertEqual(recovered["items"][0]["status"], "submitted" if status == "submitted" else "uncertain")

    def test_v1_write_history_recovers_and_blocks_v2_without_changing_original_artifacts(self):
        for history_count, status in enumerate(("submitted", "uncertain", "attempting"), start=1):
            with self.subTest(status=status):
                invitation_id = "legacy-" + status
                with patch.object(core, "IMPLEMENTATION_VERSION", "target-cleanup-v1"):
                    first, snapshot = self._preview(key=status, identifiers=(invitation_id,))
                    execution = self._execution(first, key=status + "-execute")
                    results = self._write_results(first["batch_id"], [status])
                    paths = service.artifact_paths(first["batch_id"])
                    core.atomic_write_json(paths["backup_json"], snapshot, immutable=True)
                    core.write_csv_report(paths["backup_csv"], results["items"], results=True, immutable=True)
                original_files = {path: path.read_bytes() for path in paths.values() if path.exists()}
                self._job_status(execution["job_id"], "interrupted")
                second, current_snapshot = self._preview(key=status + "-v2", identifiers=(invitation_id,))
                self.assertEqual(current_snapshot["implementation_version"], "target-cleanup-v2")
                self.assert_code("historical-write-requires-review", service.create_execution,
                                 self.session_factory, second["batch_id"], "y", status + "-v2-execute")
                service.recover_batch(self.other_session_factory, first["batch_id"])
                recovered = service.batch_payload(self.session_factory, first["batch_id"])
                expected_status = "submitted" if status == "submitted" else "uncertain"
                self.assertEqual(recovered["items"][0]["status"], expected_status)
                self.assertEqual(recovered["execute_status"], "completed" if status == "submitted" else "needs_review")
                self.assertEqual(recovered["frozen"]["implementation_version"], "target-cleanup-v1")
                self.assertEqual(recovered["error_code"], "execution-incomplete")
                self.assertIn("results_csv", recovered["downloads"])
                self.assertIn(expected_status.encode(), paths["results_csv"].read_bytes())
                self.assertEqual(core.read_execution_results(paths["results_json"], snapshot,
                                                            core.payload_digest(snapshot))[0]["status"], status)
                for path, original_bytes in original_files.items():
                    self.assertEqual(path.read_bytes(), original_bytes)
                current = service.batch_payload(self.session_factory, second["batch_id"])
                self.assertFalse(current["can_execute"])
                self.assertEqual(current["execute_status"], "unclaimed")
                with self.session_factory() as session:
                    self.assertEqual(session.scalar(select(func.count()).select_from(Job).where(
                        Job.job_type == service.EXECUTE_JOB_TYPE)), history_count)

    def test_get_uses_cached_database_state_bounded_items_and_only_csv_links(self):
        preview, _snapshot = self._preview(identifiers=tuple("invite-" + str(index) for index in range(105)))
        self.running_mock.side_effect = AssertionError("GET must not inspect running stores")
        self.page_mock.side_effect = AssertionError("GET must not probe the page")
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertEqual(len(payload["items"]), 100)
        self.assertEqual(payload["total"], 105)
        self.assertEqual(payload["nonzero_count"], 105)
        self.assertTrue(payload["can_execute"])
        self.assertEqual(len(service.batch_payload(self.session_factory, preview["batch_id"], offset=100)["items"]), 5)
        self.assertTrue(all(".csv" in link and ".json" not in link for link in payload["downloads"].values()))
        self.assertEqual(service.list_batches(self.session_factory, idempotency_key="preview-key")["total"], 1)
        self.assert_code("invalid-pagination", service.batch_payload, self.session_factory, preview["batch_id"], limit=101)
        self.assert_code("invalid-pagination", service.list_batches, self.session_factory, limit=21)

    def test_fail_batch_prelaunch_preserves_no_write_status(self):
        preview, _snapshot = self._preview()
        self._execution(preview, begin=False)
        service.fail_batch(self.session_factory, preview["batch_id"], "execute", "Missing script resource")
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertEqual(payload["execute_status"], "partial")
        self.assertEqual(payload["counts"]["not_processed"], 2)
        self.assertEqual(payload["counts"]["uncertain"], 0)


    def test_core_callbacks_end_to_end_stop_at_uncertain_without_replaying(self):
        preview, snapshot = self._preview(identifiers=("first", "second", "third"))
        execution = self._execution(preview)
        cancellations = []
        def cancel_target(store_id, **parameters):
            cancellations.append(parameters["invitation_id"])
            return {
                "status": "submitted" if len(cancellations) == 1 else "uncertain",
                "action": "cancel:submitted" if len(cancellations) == 1 else "cancel:unknown",
                "reason": "fake boundary outcome",
            }
        report = core.execute_snapshot(
            snapshot, directory=service.cleanup_directory(), snapshot_sha256=core.payload_digest(snapshot),
            execute=True, yes=True,
            before_attempt=lambda invitation_id: service.mark_item_attempting(self.session_factory, preview["batch_id"], invitation_id),
            on_result=lambda result: service.save_item_result(self.session_factory, preview["batch_id"], result),
            validate_identity=lambda: service.validate_script_identity(self.session_factory, preview["batch_id"], execution["job_id"]),
            locate_fn=lambda *arguments, **keywords: {"revalidated": True},
            cancel_fn=cancel_target, sleep_fn=lambda seconds: None,
        )
        service.finish_execution(self.session_factory, preview["batch_id"], report["status"])
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertEqual(cancellations, ["first", "second"])
        self.assertEqual(payload["execute_status"], "needs_review")
        self.assertEqual([item["status"] for item in payload["items"]], ["submitted", "uncertain", "not_processed"])
        self.assertTrue(service.artifact_paths(preview["batch_id"])["backup_csv"].is_file())

    def test_execution_envelope_failure_does_not_publish_job_or_key(self):
        preview, _snapshot = self._preview()
        with patch.object(core, "atomic_write_json", side_effect=OSError("disk full")):
            self.assert_code("envelope-write-failed", service.create_execution, self.session_factory, preview["batch_id"], "y", "key")
        with self.session_factory() as session:
            batch = session.get(TargetCleanupBatch, preview["batch_id"])
            self.assertIsNone(batch.execute_job_id)
            self.assertIsNone(batch.execute_idempotency_key)
            self.assertEqual(batch.execute_status, "unclaimed")
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)

    def test_per_row_identity_guard_checks_busy_before_probe_and_exact_job(self):
        preview, _snapshot = self._preview()
        execution = self._execution(preview)
        self.running_mock.reset_mock()
        self.page_mock.reset_mock()
        self.assert_code("job-claim-required", service.validate_script_identity, self.session_factory, preview["batch_id"], "not-server-owned")
        with self.session_factory() as session:
            session.add(Job(id="busy", job_type="operator_prepare", status="pending"))
            session.commit()
        self.assert_code("store-busy", service.validate_script_identity, self.session_factory, preview["batch_id"], execution["job_id"])
        self.running_mock.assert_not_called()
        self.page_mock.assert_not_called()

    def test_queued_preview_abandonment_is_terminal_not_an_empty_preview(self):
        preview = service.create_preview(self.session_factory, 4, "preview-key")
        self._job_status(preview["job_id"], "cancelled")
        service.recover_batch(self.session_factory, preview["batch_id"])
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertEqual(payload["preview_status"], "failed")
        self.assertEqual(payload["error_code"], "preview-abandoned")
        self.assertFalse(payload["can_execute"])


    def test_commit_failure_orphan_envelope_can_be_recovered_without_any_job_or_attempt(self):
        preview, _snapshot = self._preview()
        with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("Synthetic commit failure")):
            with self.assertRaises(RuntimeError):
                service.create_execution(self.session_factory, preview["batch_id"], "y", "retry-key")
        path = service.artifact_paths(preview["batch_id"])["execute_envelope"]
        orphan = json.loads(path.read_bytes())
        with self.session_factory() as session:
            self.assertIsNone(session.get(Job, orphan["job_id"]))
            self.assertEqual(session.get(TargetCleanupBatch, preview["batch_id"]).execute_status, "unclaimed")
        recovered = service.create_execution(self.session_factory, preview["batch_id"], "y", "retry-key")
        receipt = json.loads(path.read_bytes())
        self.assertEqual(receipt["job_id"], recovered["job_id"])
        self.assertNotEqual(receipt["job_id"], orphan["job_id"])
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 2)
            self.assertEqual(session.get(TargetCleanupBatch, preview["batch_id"]).execute_status, "queued")

    def test_orphan_envelope_with_any_execution_artifact_is_not_replaced(self):
        preview, _snapshot = self._preview()
        with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("Synthetic commit failure")):
            with self.assertRaises(RuntimeError):
                service.create_execution(self.session_factory, preview["batch_id"], "y", "retry-key")
        paths = service.artifact_paths(preview["batch_id"])
        original_envelope = paths["execute_envelope"].read_bytes()
        core.atomic_write_json(paths["results_json"], {"state": "unknown"})
        self.assert_code("orphan-execution-requires-review", service.create_execution,
                         self.session_factory, preview["batch_id"], "y", "retry-key")
        self.assertEqual(paths["execute_envelope"].read_bytes(), original_envelope)
        self.assertEqual(json.loads(paths["results_json"].read_bytes()), {"state": "unknown"})

    def test_recovery_rebuilds_csv_from_committed_uncertain_and_unprocessed_results(self):
        import csv

        preview, _snapshot = self._preview()
        execution = self._execution(preview)
        journal = self._write_results(preview["batch_id"], ["attempting", "pending"])
        paths = service.artifact_paths(preview["batch_id"])
        core.write_csv_report(paths["results_csv"], journal["items"], results=True)
        self._job_status(execution["job_id"], "interrupted")
        service.recover_batch(self.session_factory, preview["batch_id"])
        with paths["results_csv"].open(encoding="utf-8-sig", newline="") as stream:
            statuses = [row["status"] for row in csv.DictReader(stream)]
        self.assertEqual(statuses, ["uncertain", "not_processed"])
        self.assertEqual(json.loads(paths["results_json"].read_bytes()), journal)
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertTrue(payload["results_csv_ready"])
        self.assertIn("results_csv", payload["downloads"])

    def test_failed_recovery_csv_write_preserves_database_and_hides_stale_download(self):
        preview, _snapshot = self._preview()
        execution = self._execution(preview)
        journal = self._write_results(preview["batch_id"], ["attempting", "pending"])
        paths = service.artifact_paths(preview["batch_id"])
        core.write_csv_report(paths["results_csv"], journal["items"], results=True)
        original_csv = paths["results_csv"].read_bytes()
        self._job_status(execution["job_id"], "interrupted")
        with patch.object(core, "write_csv_report", side_effect=OSError("Synthetic CSV failure")):
            service.recover_batch(self.session_factory, preview["batch_id"])
        payload = service.batch_payload(self.session_factory, preview["batch_id"])
        self.assertEqual([item["status"] for item in payload["items"]], ["uncertain", "not_processed"])
        self.assertFalse(payload["results_csv_ready"])
        self.assertNotIn("results_csv", payload["downloads"])
        self.assertIn("candidates_csv", payload["downloads"])
        self.assertEqual(paths["results_csv"].read_bytes(), original_csv)


if __name__ == "__main__":
    unittest.main()
