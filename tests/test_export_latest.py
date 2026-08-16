from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.export_util import (  # noqa: E402
    latest_screen_export,
    resolve_from_export_arg,
    write_reports,
)


class LatestScreenExportTests(unittest.TestCase):
    def test_picks_newest_and_skips_pre_execute(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            older = folder / "sample_screen_20260815_090000.json"
            newer = folder / "sample_screen_20260815_100000.json"
            backup = folder / "sample_screen_20260815_100100_pre_execute.json"
            older.write_text("[]", encoding="utf-8")
            newer.write_text("[]", encoding="utf-8")
            backup.write_text("[]", encoding="utf-8")
            self.assertEqual(latest_screen_export(folder), newer)
            self.assertEqual(resolve_from_export_arg(str(newer)), newer)

    def test_missing_export_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(FileNotFoundError):
                latest_screen_export(Path(raw))

    def test_blank_or_latest_uses_helper(self) -> None:
        self.assertIsNone(resolve_from_export_arg(None))

    def test_write_reports_skips_xlsx(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            prefix = Path(raw) / "sample_screen_x"
            paths = write_reports([{"creator_name": "a", "eligible": True}], prefix)
            self.assertIn("csv", paths)
            self.assertIn("json", paths)
            self.assertNotIn("xlsx", paths)
            self.assertTrue(paths["csv"].is_file())
            self.assertTrue(paths["json"].is_file())
            self.assertFalse(prefix.with_suffix(".xlsx").exists())


if __name__ == "__main__":
    unittest.main()
