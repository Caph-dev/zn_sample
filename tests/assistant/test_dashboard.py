from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from assistant.api.dashboard import dashboard_summary
from assistant.database.engine import create_database_engine
from assistant.database.models import Base


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
