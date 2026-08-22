from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from assistant.api.dashboard import dashboard_summary
from assistant.database.engine import create_database_engine
from assistant.database.models import Base, FollowupTask, Job, SampleCase, Shipment, Store


class DashboardTests(unittest.TestCase):
    def test_empty_dashboard_has_all_required_counters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = create_database_engine(Path(directory) / "test.sqlite3")
            Base.metadata.create_all(engine)
            summary = dashboard_summary(sessionmaker(bind=engine, expire_on_commit=False))
            self.assertEqual(summary["waiting_delivery"], 0)
            self.assertIn("day_10_list", summary)
            self.assertIn("needs_review", summary)
            engine.dispose()

    def test_counts_only_active_processing_followups_and_reports_job_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = create_database_engine(Path(directory) / "test.sqlite3")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                store = Store(ziniao_store_id="store", store_name="store")
                session.add(store); session.flush()
                for status, task_status in ((30, "pending"), (40, "pending"), (40, "suppressed")):
                    sample_case = SampleCase(store_id=store.id, creator_id=f"c-{status}-{task_status}", creator_name="creator", apply_id=f"a-{status}-{task_status}", product_id="product", curr_status=status)
                    session.add(sample_case); session.flush()
                    session.add(FollowupTask(sample_case_id=sample_case.id, stage="day_10_list", scheduled_for=date(2026, 8, 1), status=task_status, requires_manual_confirmation=True))
                    session.add(Shipment(sample_case_id=sample_case.id, status_category="exception" if status == 40 else "in_transit"))
                session.add(Job(id="job", job_type="shipment_sync", status="succeeded", created_at=datetime(2026, 8, 22, tzinfo=timezone.utc)))
                session.commit()
            summary = dashboard_summary(factory)
            self.assertEqual(summary["day_10_list"], 1)
            self.assertEqual(summary["needs_review"], 2)
            self.assertEqual(summary["logistics_exceptions"], 2)
            self.assertEqual(summary["latest_jobs"]["shipment_sync"]["status"], "succeeded")
            engine.dispose()
