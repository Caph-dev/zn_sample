from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, FollowupTask, Job, SampleCase, Shipment, Store
from assistant.jobs.handlers.followup_generate import run_followup_generate
from assistant.jobs.progress import append_event, list_events
from assistant.jobs.worker import worker_loop_once
from assistant.services.followup_service import FollowupService


class FollowupGenerationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            Path(self.temporary_directory.name) / "recovery.sqlite3"
        )

        @event.listens_for(self.engine, "checkout")
        def shorten_test_lock_timeout(connection, _record, _proxy):
            # Fail fast if a regression holds the writer during a log callback.
            connection.execute("PRAGMA busy_timeout=50")

        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.now = datetime(2026, 9, 17, tzinfo=timezone.utc)
        with self.session_factory() as session:
            store = Store(ziniao_store_id="store", store_name="Test")
            session.add(store)
            session.flush()
            for index in range(5):
                sample_case = SampleCase(
                    store_id=store.id,
                    apply_id=f"apply-{index}",
                    creator_id=f"creator-{index}",
                    creator_name=f"creator-{index}",
                    product_id="1732414717062320994",
                    platform_status="processing",
                    sample_product_option="B005",
                )
                session.add(sample_case)
                session.flush()
                session.add(Shipment(
                    sample_case_id=sample_case.id,
                    status_category="delivered",
                    delivered_at=self.now,
                ))
            session.add(Job(id="generate", job_type="followup_generate", status="pending"))
            session.commit()

        export_patch = patch("lib.export_util.latest_screen_export", side_effect=FileNotFoundError)
        export_patch.start()
        self.addCleanup(export_patch.stop)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def record_warning(self, message: str) -> None:
        append_event(
            self.session_factory, "generate", level="warning",
            event_type="job.warning", message=message,
        )

    def test_language_failures_can_write_events_without_self_deadlock(self) -> None:
        with (
            patch("lib.app_config.load_bitable_settings", return_value={}),
            patch("lib.sample_navigation.navigate_to_url"),
            patch("lib.creator_detail.extract_creator_detail", return_value={
                "detail_read_status": "page-data-unavailable",
                "detail_error_message": "Server error",
            }) as read_detail,
        ):
            result = FollowupService(
                self.session_factory, store_id="store", warning=self.record_warning,
            ).generate(self.now)

        self.assertEqual(result["created"], 5)
        self.assertEqual(read_detail.call_count, 5)
        self.assertEqual(len(list_events(self.session_factory, "generate")), 5)
        with self.session_factory() as session:
            tasks = session.scalars(select(FollowupTask)).all()
            self.assertTrue(all(task.status == "needs_review" for task in tasks))
            self.assertTrue(all(task.review_reason == "missing_creator_type" for task in tasks))

    def test_language_detection_runs_before_any_task_write_transaction(self) -> None:
        with self.session_factory() as session:
            for sample_case in session.scalars(select(SampleCase)).all():
                sample_case.bio = f"Hola from {sample_case.creator_name}"
            session.commit()

        def detect_language(_bio):
            self.record_warning("language-detection-checkpoint")
            return {"lang": "es"}

        with patch("assistant.domain.policies.detect_creator_lang", side_effect=detect_language):
            result = FollowupService(self.session_factory).generate(
                self.now, enrich_missing_language=False,
            )

        self.assertEqual(result["created"], 5)
        self.assertEqual(len(list_events(self.session_factory, "generate")), 5)
        with self.session_factory() as session:
            self.assertTrue(all(
                task.language == "es" for task in session.scalars(select(FollowupTask)).all()
            ))

    def test_systemic_failure_does_not_retry_details_or_block_next_job(self) -> None:
        with (
            patch("assistant.jobs.handlers.followup_generate.resolve_readonly_store", return_value="store"),
            patch("lib.sample_navigation.ensure_sample_request_context"),
            patch("lib.creator_api.fetch_creator_detail_api", return_value={
                "ok": False, "error": "profile business failure code=100000",
            }) as fetch_profile,
            patch("lib.app_config.load_bitable_settings", return_value={}) as feishu_settings,
            patch("lib.sample_navigation.navigate_to_url") as navigate,
            patch("assistant.jobs.worker.get_handler", return_value=run_followup_generate),
        ):
            self.assertEqual(worker_loop_once(self.session_factory), "generate")

        self.assertEqual(fetch_profile.call_count, 3)
        self.assertEqual(feishu_settings.call_count, 5)
        navigate.assert_not_called()
        with self.session_factory() as session:
            self.assertEqual(session.get(Job, "generate").status, "succeeded")
            session.add(Job(id="next-job", job_type="report_export", status="pending"))
            session.commit()
        with patch("assistant.jobs.worker.get_handler", return_value=lambda *_: "verified"):
            self.assertEqual(worker_loop_once(self.session_factory), "next-job")
        with self.session_factory() as session:
            self.assertEqual(session.get(Job, "next-job").status, "succeeded")


if __name__ == "__main__":
    unittest.main()
