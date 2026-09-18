"""Legacy batch recovery uses original evidence, never reconciliation success."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sqlalchemy.orm import sessionmaker
from assistant.database.engine import create_database_engine
from assistant.database.models import AutoApprovalExecution, AutoApprovalExecutionItem, AutoApprovalPreview, Base, Job
from assistant.services.auto_approval_recovery import plan_legacy_execution_recovery, recover_legacy_execution
from assistant.services.auto_approval_reconciliation import reconciliation_payload
from assistant.services.auto_approval_service import AutoApprovalServiceError


class LegacyRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.engine = create_database_engine(self.root / "assistant.sqlite3")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.result = {
            "envelope_type": "auto_approval_execution", "schema_version": 1,
            "execution_id": "batch", "store_id": "store", "rule_hash": "hash", "limit": 1,
            "finished_at": datetime.now(timezone.utc).isoformat(), "preview_id": "",
            "items": [{"apply_id": "apply", "creator_id": "creator", "creator_name": "name",
                       "product_id": "product", "approve_status": "approved"}],
        }
        self.result_path = self.root / "result.json"
        self.result_path.write_text(json.dumps(self.result), encoding="utf-8")
        (self.root / "backup_pre_execute.json").write_text("[]", encoding="utf-8")
        with self.sessions() as session:
            session.add(AutoApprovalPreview(id="preview", store_id="store", rule_hash="hash", rule_json="{}"))
            session.flush()
            session.add(AutoApprovalExecution(
                id="batch", preview_id="preview", store_id="store", job_id="reconcile-job",
                idempotency_key="key", rule_hash="hash", limit_count=1,
                apply_ids_json='["apply"]', status="queued", result_path=str(self.result_path),
                backup_path=str(self.root / "backup"),
            ))
            session.flush()
            session.add(AutoApprovalExecutionItem(execution_id="batch", **self.result["items"][0]))
            for job_id, job_type in (("approval-job", "auto_approval_execute"), ("reconcile-job", "auto_approval_reconcile")):
                session.add(Job(id=job_id, job_type=job_type, store_id="store", status="succeeded",
                                result_summary=json.dumps({"execution_id": "batch", "preview_id": "preview"}),
                                finished_at=datetime.now(timezone.utc)))
            session.commit()

    def plan(self):
        with self.sessions() as session:
            return plan_legacy_execution_recovery(session, "batch")

    def test_recovery_backs_up_and_restores_only_batch_metadata(self):
        plan = self.plan()
        self.assertEqual(plan["after"], {"status": "completed", "job_id": "approval-job"})
        with self.sessions() as session:
            self.assertEqual(session.get(AutoApprovalExecution, "batch").status, "queued")
        result = recover_legacy_execution(self.sessions, "batch", backup_directory=self.root / "recovery")
        with sqlite3.connect(result["database_backup"]) as connection:
            self.assertEqual(connection.execute("SELECT status,job_id FROM auto_approval_executions").fetchone(), ("queued", "reconcile-job"))
        with self.sessions() as session:
            execution = session.get(AutoApprovalExecution, "batch")
            self.assertEqual((execution.status, execution.job_id), ("completed", "approval-job"))
            payload = reconciliation_payload(session, execution)
            self.assertTrue(payload["available"])
            self.assertFalse(payload["can_repair"])
            self.assertEqual(payload["latest_job"]["job_id"], "reconcile-job")
        self.assertFalse(self.plan()["changed"])

    def test_queued_with_terminal_reconcile_is_anomaly_not_running(self):
        with self.sessions() as session:
            payload = reconciliation_payload(session, session.get(AutoApprovalExecution, "batch"))
        self.assertFalse(payload["approval_active"])
        self.assertIn("历史批次状态异常", payload["state_error"])
        self.assertFalse(payload["available"])

    def test_actual_approval_job_still_counts_as_running(self):
        with self.sessions() as session:
            session.get(AutoApprovalExecution, "batch").job_id = "approval-job"
            session.get(Job, "approval-job").status = "running"
            session.commit()
            payload = reconciliation_payload(session, session.get(AutoApprovalExecution, "batch"))
        self.assertTrue(payload["approval_active"])
        self.assertEqual(payload["state_error"], "")
        self.assertFalse(payload["available"])

    def test_failed_or_active_original_is_never_inferred_successful(self):
        for status in ("failed", "interrupted", "pending", "running"):
            with self.subTest(status=status), self.sessions() as session:
                session.get(Job, "approval-job").status = status
                session.commit()
                with self.assertRaises(AutoApprovalServiceError):
                    self.plan()

    def test_tampered_or_missing_evidence_never_changes_state(self):
        for field in ("execution_id", "store_id", "rule_hash", "limit"):
            with self.subTest(field=field):
                self.result_path.write_text(json.dumps({**self.result, field: "wrong"}))
                with self.assertRaises(AutoApprovalServiceError):
                    self.plan()
        self.result_path.unlink()
        with self.assertRaises(AutoApprovalServiceError):
            self.plan()

    def test_row_identity_mismatch_and_duplicate_original_jobs_block_recovery(self):
        self.result["items"][0]["creator_id"] = "other"
        self.result_path.write_text(json.dumps(self.result))
        with self.assertRaises(AutoApprovalServiceError):
            self.plan()
        self.result["items"][0]["creator_id"] = "creator"
        self.result_path.write_text(json.dumps(self.result))
        with self.sessions() as session:
            session.add(Job(id="duplicate", job_type="auto_approval_execute", store_id="store", status="succeeded",
                            result_summary='{"execution_id":"batch"}', finished_at=datetime.now(timezone.utc)))
            session.commit()
        with self.assertRaises(AutoApprovalServiceError):
            self.plan()

    def test_unknown_approval_restores_needs_reconcile_not_completed(self):
        self.result["items"][0]["approve_status"] = "unknown"
        self.result_path.write_text(json.dumps(self.result))
        with self.sessions() as session:
            from sqlalchemy import select
            session.scalar(select(AutoApprovalExecutionItem)).approve_status = "unknown"
            session.commit()
        self.assertEqual(self.plan()["after"]["status"], "needs_reconcile")
