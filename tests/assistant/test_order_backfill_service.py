"""订单号补写服务与任务 handler：确认门闩、并发保护、报告读取。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sqlalchemy.orm import sessionmaker

from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import Base, Job
from assistant.jobs.handlers.auto_approval import _fixed_argv
from assistant.jobs.registry import WRITE_JOB_TYPES, ZINIAO_JOB_TYPES, get_handler
from assistant.services import auto_approval_service as approval
from assistant.services import order_backfill as service
from fastapi.testclient import TestClient


class OrderBackfillServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.engine = create_database_engine(self.root / "assistant.sqlite3")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.object(service.approval, "auto_approval_directory", return_value=self.root)
        )
        self.blocking_jobs = self.stack.enter_context(
            patch.object(service, "blocking_jobs", return_value=[])
        )
        self.saved_requests = []
        self.deduplicated = False
        self.stack.enter_context(patch.object(approval, "_create_job", side_effect=self.save_job))

    def save_job(self, session_factory, *, job_type, store_id, result_summary):
        self.saved_requests.append(json.loads(result_summary))
        if self.deduplicated:
            return "existing-job", True
        job_id = f"job-{len(self.saved_requests)}"
        with session_factory() as session:
            session.add(Job(
                id=job_id, job_type=job_type, store_id=store_id,
                status="pending", result_summary=result_summary,
            ))
            session.commit()
        return job_id, False

    def test_confirmation_is_required_and_nothing_is_created(self):
        with self.assertRaises(approval.AutoApprovalServiceError) as context:
            service.create_order_backfill(
                self.session_factory, store_id="store-1", limit=0, confirmation=""
            )
        self.assertEqual(context.exception.code, "confirmation-required")
        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(self.saved_requests, [])

    def test_store_is_required(self):
        with self.assertRaises(approval.AutoApprovalServiceError) as context:
            service.create_order_backfill(
                self.session_factory, store_id="  ", limit=0, confirmation="y"
            )
        self.assertEqual(context.exception.code, "store-required")

    def test_invalid_limit_is_rejected_and_zero_means_unlimited(self):
        with self.assertRaises(approval.AutoApprovalServiceError) as context:
            service.create_order_backfill(
                self.session_factory, store_id="store-1", limit="many", confirmation="y"
            )
        self.assertEqual(context.exception.code, "limit-invalid")

        created = service.create_order_backfill(
            self.session_factory, store_id="store-1", limit=None, confirmation="y"
        )
        self.assertEqual(created["limit"], 0)
        self.assertEqual(self.saved_requests[0]["limit"], 0)
        self.assertTrue(self.saved_requests[0]["write_feishu"])
        self.assertEqual(self.saved_requests[0]["person"], "王良希（技术）")
        self.assertEqual(self.saved_requests[0]["lookback_hours"], 72)
        self.assertTrue(str(self.saved_requests[0]["result_path"]).startswith(str(self.root)))

    def test_page_lock_and_duplicate_jobs_return_store_busy(self):
        self.blocking_jobs.return_value = [{"id": "other", "job_type": "operator_tracking", "status": "running"}]
        with self.assertRaises(approval.AutoApprovalServiceError) as context:
            service.create_order_backfill(
                self.session_factory, store_id="store-1", limit=0, confirmation="y"
            )
        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.code, "store-busy")

        self.blocking_jobs.return_value = []
        self.deduplicated = True
        with self.assertRaises(approval.AutoApprovalServiceError) as context:
            service.create_order_backfill(
                self.session_factory, store_id="store-1", limit=0, confirmation="y"
            )
        self.assertEqual(context.exception.code, "store-busy")

    def test_state_blocks_while_the_job_runs_and_exposes_the_report(self):
        state = service.state_payload(self.session_factory)
        self.assertTrue(state["available"])
        self.assertEqual(state["latest_job"], {})
        self.assertIsNone(state["report"])

        created = service.create_order_backfill(
            self.session_factory, store_id="store-1", limit=0, confirmation="y"
        )
        with self.session_factory() as session:
            job = session.get(Job, created["job_id"])
            job.status = "running"
            session.commit()
        running_state = service.state_payload(self.session_factory)
        self.assertFalse(running_state["available"])
        self.assertIn("正在排队或运行", running_state["blocked_reason"])

        report_path = Path(self.saved_requests[0]["result_path"])
        report_path.write_text(json.dumps({
            "envelope_type": "order_backfill_report",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "store_id": "store-1", "person": "王良希（技术）", "lookback_hours": 72,
            "write_feishu": True, "platform_rows": 2, "search_errors": [],
            "counts": {"written": 1},
            "items": [{"record_id": "rec-1", "creator_handle": "creator_a",
                       "sample_product": "B005", "status": "written"}],
        }, ensure_ascii=False), encoding="utf-8")
        with self.session_factory() as session:
            job = session.get(Job, created["job_id"])
            job.status = "succeeded"
            session.commit()

        finished_state = service.state_payload(self.session_factory)
        self.assertTrue(finished_state["available"])
        self.assertEqual(finished_state["report"]["counts"], {"written": 1})
        self.assertEqual(finished_state["report"]["items"][0]["status"], "written")

    def test_foreign_report_envelope_is_ignored(self):
        created = service.create_order_backfill(
            self.session_factory, store_id="store-1", limit=0, confirmation="y"
        )
        Path(self.saved_requests[0]["result_path"]).write_text(
            json.dumps({"envelope_type": "auto_approval_reconcile", "items": []}),
            encoding="utf-8",
        )
        self.assertIsNone(service.state_payload(self.session_factory)["report"])
        self.assertTrue(created["job_id"])

    def test_candidates_payload_is_read_only_and_reports_scan_errors(self):
        rows = [{"record_id": "rec-1", "creator_handle": "creator_a", "sample_product": "B005"}]
        with patch("lib.feishu_bitable.get_bitable_access_token", return_value="token"):
            with patch("lib.order_backfill.collect_order_backfill_candidates", return_value=rows) as collect:
                payload = service.candidates_payload()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["person"], "王良希（技术）")
        self.assertEqual(collect.call_args.kwargs["lookback_hours"], 72)

        with patch.dict(sys.modules, {"lib.order_backfill": None}):
            with self.assertRaises(approval.AutoApprovalServiceError) as context:
                service.candidates_payload()
        self.assertEqual(context.exception.code, "feishu-unavailable")
        self.assertEqual(context.exception.status_code, 502)


class OrderBackfillHandlerTests(unittest.TestCase):
    def test_job_type_is_registered_as_a_protected_write_job(self):
        self.assertIn("auto_approval_order_backfill", ZINIAO_JOB_TYPES)
        self.assertIn("auto_approval_order_backfill", WRITE_JOB_TYPES)
        self.assertIsNotNone(get_handler("auto_approval_order_backfill"))

    def test_argv_is_fixed_and_needs_no_rule_snapshot(self):
        argv = _fixed_argv(
            {
                "store_id": "store-1", "result_path": "/tmp/order_backfill.json",
                "limit": 0, "write_feishu": True,
            },
            "order-backfill",
        )
        self.assertEqual(argv[argv.index("--mode") + 1], "order-backfill")
        self.assertEqual(argv[argv.index("--store-id") + 1], "store-1")
        self.assertEqual(argv[argv.index("--out") + 1], "/tmp/order_backfill.json")
        self.assertEqual(argv[argv.index("--limit") + 1], "0")
        self.assertEqual(argv[argv.index("--write-feishu") + 1], "1")
        self.assertIn("--yes", argv)
        self.assertNotIn("--rules", argv)
        self.assertNotIn("--execution-id", argv)

    def test_read_only_variant_drops_the_yes_flag(self):
        argv = _fixed_argv(
            {
                "store_id": "store-1", "result_path": "/tmp/order_backfill.json",
                "limit": 3, "write_feishu": False,
            },
            "order-backfill",
        )
        self.assertEqual(argv[argv.index("--write-feishu") + 1], "0")
        self.assertNotIn("--yes", argv)

    def test_missing_result_path_fails_closed(self):
        from assistant.jobs.registry import HandlerFailure

        with self.assertRaises(HandlerFailure):
            _fixed_argv({"store_id": "store-1"}, "order-backfill")


class OrderBackfillApiTests(unittest.TestCase):
    """路由接线：只读扫描、状态、以及缺 y 时拒绝创建写任务。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = create_database_engine(Path(self.directory.name) / "assistant.sqlite3")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.client = TestClient(create_app(port=8765), base_url="http://127.0.0.1:8765")
        self.addCleanup(self.client.close)
        self.client.app.state.session_factory = self.session_factory
        self.headers = {"Origin": "http://127.0.0.1:8765"}

    def test_candidates_and_state_are_read_only(self):
        with patch("lib.feishu_bitable.get_bitable_access_token", return_value="token"):
            with patch("lib.order_backfill.collect_order_backfill_candidates", return_value=[]):
                candidates = self.client.get(
                    "/api/auto-approval/order-backfill/candidates", headers=self.headers
                )
        self.assertEqual(candidates.status_code, 200)
        self.assertEqual(candidates.json()["total"], 0)
        self.assertEqual(candidates.json()["person"], "王良希（技术）")

        state = self.client.get("/api/auto-approval/order-backfill", headers=self.headers)
        self.assertEqual(state.status_code, 200)
        self.assertTrue(state.json()["available"])
        self.assertIsNone(state.json()["report"])

    def test_write_requires_confirmation_and_store(self):
        rejected = self.client.post(
            "/api/auto-approval/order-backfill",
            json={"store_id": "store-1", "limit": 0, "confirmation": ""},
            headers=self.headers,
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()["detail"]["code"], "confirmation-required")

        with patch.object(service, "blocking_jobs", return_value=[]):
            with patch.object(service.approval, "auto_approval_directory", return_value=Path(self.directory.name)):
                with patch.object(approval, "_create_job", side_effect=self._save_job):
                    created = self.client.post(
                        "/api/auto-approval/order-backfill",
                        json={"store_id": "store-1", "limit": 0, "confirmation": "y"},
                        headers=self.headers,
                    )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["job_id"], "api-job-1")
        with self.session_factory() as session:
            job = session.get(Job, "api-job-1")
            self.assertEqual(job.job_type, "auto_approval_order_backfill")
            self.assertEqual(job.status, "pending")

    def _save_job(self, session_factory, *, job_type, store_id, result_summary):
        with session_factory() as session:
            session.add(Job(
                id="api-job-1", job_type=job_type, store_id=store_id,
                status="pending", result_summary=result_summary,
            ))
            session.commit()
        return "api-job-1", False


if __name__ == "__main__":
    unittest.main()
