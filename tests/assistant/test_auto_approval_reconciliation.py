"""Reconciliation service and handler safety, isolated SQLite and JSON fixtures."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from assistant.api.auto_approval import list_executions
from assistant.database.engine import create_database_engine
from assistant.database.models import AutoApprovalExecution, AutoApprovalPreview, Base, Job
from assistant.jobs.handlers.auto_approval import _fixed_argv
from assistant.services import auto_approval_reconciliation as reconciliation
from assistant.services import auto_approval_service as approval


class ReconciliationServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.engine = create_database_engine(self.root / "assistant.sqlite3")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.rows = [{
            "apply_id": "apply-1", "creator_id": "creator-1", "creator_name": "creator_test",
            "product_id": "1732414717062320994", "approve_status": "approved",
            "feishu_relation_status": "not-requested", "sample_product_option": "B005",
        }]
        self.backup_path = self.root / "original_pre_execute.json"
        self.backup_path.write_text(json.dumps(self.rows), encoding="utf-8")
        self.rules_path = self.root / "rules.json"
        self.preview_path = self.root / "preview.json"
        self.rules_path.write_text("{}", encoding="utf-8")
        self.preview_path.write_text("{}", encoding="utf-8")
        with self.session_factory() as session:
            session.add(AutoApprovalPreview(
                id="preview-1", store_id="store-1", job_id="original-preview-job", rule_json="{}",
                rule_hash="hash-1", rules_path=str(self.rules_path), result_path=str(self.preview_path),
                status="completed", integrity_complete=True,
                finished_at=datetime.now(timezone.utc) - timedelta(days=3),
            ))
            session.flush()
            session.add(AutoApprovalExecution(
                id="execution-1", preview_id="preview-1", job_id="original-approval-job",
                idempotency_key="original-key", store_id="store-1", rule_hash="hash-1",
                apply_ids_json='["apply-1"]', limit_count=3, status="needs_reconcile",
                result_path=str(self.root / "original_result.json"), backup_path=str(self.root / "original"),
            ))
            session.commit()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(approval, "rule_snapshot_directory", return_value=self.root))
        self.stack.enter_context(patch.object(approval, "auto_approval_directory", return_value=self.root))
        self.stack.enter_context(patch.object(reconciliation, "blocking_jobs", return_value=[]))
        self.create_job = self.stack.enter_context(patch.object(approval, "_create_job", side_effect=self.save_job))
        self.saved_requests = []
        self.report_sequence = 0

    def save_job(self, session_factory, *, job_type, store_id, result_summary):
        request = json.loads(result_summary)
        self.saved_requests.append(request)
        job_id = f"new-job-{len(self.saved_requests)}"
        with session_factory() as session:
            session.add(Job(id=job_id, job_type=job_type, store_id=store_id, status="pending", result_summary=result_summary))
            session.commit()
        return job_id, False

    def create(self, *, write_feishu=False, confirmation=""):
        return approval.create_reconcile(
            self.session_factory, execution_id="execution-1", write_feishu=write_feishu,
            confirmation=confirmation,
        )

    def add_report(self, *, status="succeeded", write_feishu=False, report_exists=True, **overrides):
        self.report_sequence += 1
        report_path = self.root / f"report-{self.report_sequence}.json"
        report = {
            "envelope_type": "auto_approval_reconcile", "schema_version": 1,
            "execution_id": "execution-1", "store_id": "store-1",
            "checked_at": datetime.now(timezone.utc).isoformat(), "write_feishu": write_feishu,
            "rows": [dict(row) for row in self.rows],
            "items": [{"apply_id": "apply-1", "status": "missing", "can_repair": True}],
        }
        report.update(overrides)
        if report_exists:
            report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.session_factory() as session:
            session.add(Job(
                id=f"report-job-{self.report_sequence}", job_type="auto_approval_reconcile",
                store_id="store-1", status=status,
                created_at=datetime.now(timezone.utc) + timedelta(seconds=self.report_sequence),
                result_summary=json.dumps({"execution_id": "execution-1", "result_path": str(report_path), "write_feishu": write_feishu}),
            ))
            session.commit()
        return report_path

    def assert_rejected(self, expected_code, **kwargs):
        with self.assertRaises(approval.AutoApprovalServiceError) as context:
            self.create(**kwargs)
        self.assertEqual(context.exception.code, expected_code)
        self.create_job.assert_not_called()

    def test_read_only_without_confirmation_preserves_original_execution(self):
        original_backup = self.backup_path.read_bytes()
        result = self.create()
        self.assertEqual(result["job_id"], "new-job-1")
        request = self.saved_requests[0]
        self.assertFalse(request["write_feishu"])
        self.assertEqual(request["limit"], 3)
        snapshot = json.loads(Path(request["backup_path"]).read_text())
        self.assertEqual(snapshot["repair_apply_ids"], [])
        self.assertEqual(snapshot["rows"], self.rows)
        self.assertEqual(snapshot["store_id"], "store-1")
        self.assertEqual(snapshot["execution_id"], "execution-1")
        self.assertEqual(snapshot["limit"], 3)
        self.assertEqual(self.backup_path.read_bytes(), original_backup)
        with self.session_factory() as session:
            original = session.get(AutoApprovalExecution, "execution-1")
            self.assertEqual(original.status, "needs_reconcile")
            self.assertEqual(original.job_id, "original-approval-job")
            self.assertEqual(original.result_path, str(self.root / "original_result.json"))

    def test_write_requires_y_and_latest_completed_missing_report(self):
        self.assert_rejected("confirmation-required", write_feishu=True, confirmation="")
        self.assert_rejected("verification-required", write_feishu=True, confirmation="y")
        self.add_report()
        result = self.create(write_feishu=True, confirmation="y")
        self.assertEqual(result["job_id"], "new-job-1")
        request = self.saved_requests[0]
        self.assertTrue(request["write_feishu"])
        snapshot = json.loads(Path(request["backup_path"]).read_text())
        self.assertEqual(snapshot["repair_apply_ids"], ["apply-1"])

    def test_failed_running_and_pending_reports_never_authorize_writes(self):
        self.add_report()
        for status in ("failed", "running", "pending"):
            with self.subTest(status=status):
                self.add_report(status=status)
                self.assert_rejected(
                    "verification-required" if status == "failed" else "reconciliation-unavailable",
                    write_feishu=True, confirmation="y",
                )

    def test_stale_future_or_no_missing_report_never_authorizes_writes(self):
        for overrides in (
            {"checked_at": (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()},
            {"checked_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()},
            {"checked_at": "invalid"},
            {"items": [{"apply_id": "apply-1", "status": "verified", "can_repair": False}]},
        ):
            with self.subTest(overrides=overrides):
                self.add_report(**overrides)
                self.assert_rejected("verification-required", write_feishu=True, confirmation="y")

    def test_failed_later_read_carries_previous_uncertain_write(self):
        uncertain_rows = [{**self.rows[0], "feishu_relation_status": "write-uncertain", "order_backfill_status": "write-uncertain"}]
        self.add_report(
            status="failed", write_feishu=True, rows=uncertain_rows,
            items=[{"apply_id": "apply-1", "status": "needs_review", "can_repair": False}],
        )
        self.add_report(status="failed", report_exists=False)
        self.create()
        snapshot = json.loads(Path(self.saved_requests[0]["backup_path"]).read_text())
        self.assertEqual(snapshot["rows"][0]["feishu_relation_status"], "write-uncertain")
        self.assertEqual(snapshot["rows"][0]["order_backfill_status"], "write-uncertain")

    def test_missing_write_result_cannot_fall_back_to_earlier_missing_report(self):
        self.add_report()
        self.add_report(status="failed", write_feishu=True, report_exists=False)
        self.assert_rejected("verification-required", write_feishu=True, confirmation="y")
        self.create()
        snapshot = json.loads(Path(self.saved_requests[0]["backup_path"]).read_text())
        self.assertEqual(snapshot["rows"][0]["feishu_relation_status"], "write-uncertain")
        self.assertEqual(snapshot["rows"][0]["order_backfill_status"], "write-uncertain")

    def test_backup_absence_blocks_even_when_report_is_repairable(self):
        self.add_report()
        self.backup_path.unlink()
        self.assert_rejected("reconciliation-unavailable")
        self.assert_rejected("reconciliation-unavailable", write_feishu=True, confirmation="y")

    def test_backup_missing_selected_row_or_duplicate_is_rejected(self):
        for rows in ([], [{**self.rows[0], "apply_id": "other-apply"}], self.rows * 2):
            with self.subTest(rows=rows):
                self.backup_path.write_text(json.dumps(rows))
                self.assert_rejected("backup-invalid")

    def test_cross_batch_and_store_reports_do_not_authorize_repair(self):
        for overrides in ({"execution_id": "other-execution"}, {"store_id": "other-store"}):
            with self.subTest(overrides=overrides):
                self.add_report(**overrides)
                self.assert_rejected("verification-required", write_feishu=True, confirmation="y")

    def test_previous_row_identity_mismatch_is_rejected(self):
        self.add_report(rows=[{**self.rows[0], "creator_id": "other-creator"}])
        self.assert_rejected("reconciliation-identity-mismatch")

    def test_each_reconciliation_uses_independent_result_and_snapshot_paths(self):
        first = self.create()
        with self.session_factory() as session:
            session.get(Job, first["job_id"]).status = "failed"
            session.commit()
        second = self.create()
        self.assertNotEqual(first["job_id"], second["job_id"])
        for key in ("result_path", "backup_path"):
            self.assertNotEqual(self.saved_requests[0][key], self.saved_requests[1][key])
        self.assertNotEqual(self.saved_requests[0]["result_path"], str(self.root / "original_result.json"))

    def test_worker_failure_preserves_batch_and_report_pointers(self):
        from assistant.jobs.worker import _finish_job

        created = self.create()
        _finish_job(
            self.session_factory, created["job_id"], status="failed",
            error_code="handler-error", error_summary="read-back unavailable",
        )
        with self.session_factory() as session:
            job = session.get(Job, created["job_id"])
            self.assertEqual(json.loads(job.result_summary), self.saved_requests[0])
            execution = session.get(AutoApprovalExecution, "execution-1")
            payload = reconciliation.reconciliation_payload(session, execution)
        self.assertEqual(payload["latest_job"]["status"], "failed")
        self.assertEqual(payload["latest_job"]["error_summary"], "read-back unavailable")
        self.assertFalse(payload["can_repair"])

    def test_history_paginates_original_batches_not_reconciliation_jobs(self):
        with self.session_factory() as session:
            for index in range(2, 5):
                session.add(AutoApprovalExecution(
                    id=f"execution-{index}", preview_id="preview-1", job_id=f"original-{index}",
                    idempotency_key=f"key-{index}", store_id="store-1", rule_hash="hash-1",
                    apply_ids_json='["apply-1"]', status="completed",
                    created_at=datetime.now(timezone.utc) + timedelta(minutes=index),
                ))
            session.commit()
        self.add_report()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_factory=self.session_factory)))
        first_page = list_executions(request, offset=0, limit=2)
        second_page = list_executions(request, offset=2, limit=2)
        self.assertTrue(first_page["has_more"])
        self.assertFalse(second_page["has_more"])
        self.assertEqual([row["execution_id"] for row in first_page["executions"]], ["execution-4", "execution-3"])
        self.assertEqual([row["execution_id"] for row in second_page["executions"]], ["execution-2", "execution-1"])
        with self.session_factory() as session:
            self.assertEqual(len(session.scalars(select(Job)).all()), 1)


class ReconciliationHandlerTests(unittest.TestCase):
    def test_read_only_argv_omits_yes_and_preserves_original_limit(self):
        request = {
            "store_id": "store-1", "rules_path": "/tmp/rules.json", "result_path": "/tmp/result.json",
            "preview_path": "/tmp/preview.json", "backup_path": "/tmp/snapshot.json",
            "execution_id": "execution-1", "limit": 7, "write_feishu": False,
        }
        argv = _fixed_argv(request, "reconcile")
        self.assertNotIn("--yes", argv)
        self.assertEqual(argv[argv.index("--limit") + 1], "7")
        self.assertEqual(argv[argv.index("--write-feishu") + 1], "0")
        self.assertEqual(argv[argv.index("--backup") + 1], request["backup_path"])
        write_argv = _fixed_argv({**request, "write_feishu": True}, "reconcile")
        self.assertIn("--yes", write_argv)
        self.assertEqual(write_argv[write_argv.index("--limit") + 1], "7")
        self.assertEqual(write_argv[write_argv.index("--write-feishu") + 1], "1")


if __name__ == "__main__":
    unittest.main()
