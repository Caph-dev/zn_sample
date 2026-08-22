from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, FollowupTask, SampleCase, Shipment, Store
from assistant.services.export_service import ExportService


class ExportTests(unittest.TestCase):
    def test_day_10_export_excludes_shipped_and_keeps_ids_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_database_engine(root / "test.sqlite3")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                store = Store(ziniao_store_id="store", store_name="store"); session.add(store); session.flush()
                for status in (30, 40):
                    case = SampleCase(store_id=store.id, creator_id=str(status), creator_name="creator", apply_id=str(status), product_id="1732414717062320994", curr_status=status, main_order_id=f"order-{status}")
                    session.add(case); session.flush()
                    session.add(Shipment(sample_case_id=case.id, tracking_display=f"track-{status}"))
                    session.add(FollowupTask(sample_case_id=case.id, stage="day_10_list", scheduled_for=date(2026, 8, 1)))
                session.commit()
            path = ExportService(factory, exports_directory=root / "exports").export("day_10_list")
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("order-40", text); self.assertIn("track-40", text)
            self.assertNotIn("order-30", text)
            engine.dispose()
