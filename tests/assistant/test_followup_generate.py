from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, FollowupTask, SampleCase, Shipment, Store
from assistant.services.followup_service import FollowupService


class FollowupGenerateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(Path(self.temporary_directory.name) / "test.sqlite3")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def add_case(self, *, curr_status: int, delivered_at=None, needs_confirmation=False) -> None:
        with self.session_factory() as session:
            store = Store(ziniao_store_id="store", store_name="store")
            session.add(store); session.flush()
            case = SampleCase(store_id=store.id, creator_id="creator", creator_name="creator", apply_id=str(curr_status), product_id="1732414717062320994", curr_status=curr_status)
            session.add(case); session.flush()
            session.add(Shipment(sample_case_id=case.id, status_category="delivered", delivered_at=delivered_at, needs_delivery_time_confirmation=needs_confirmation))
            session.commit()

    def test_missing_delivery_time_creates_one_sentinel_confirmation(self) -> None:
        self.add_case(curr_status=40, needs_confirmation=True)
        service = FollowupService(self.session_factory)
        service.generate(); service.generate()
        with self.session_factory() as session:
            tasks = session.scalars(select(FollowupTask)).all()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].stage, "confirm_delivery_time")
            self.assertEqual(str(tasks[0].scheduled_for), "1970-01-01")

    def test_shipped_status_cannot_enter_day_10_calendar(self) -> None:
        self.add_case(curr_status=30, delivered_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            self.assertEqual(session.scalars(select(FollowupTask)).all(), [])
