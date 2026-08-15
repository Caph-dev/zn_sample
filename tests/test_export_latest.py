from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.export_util import latest_screen_export, resolve_from_export_arg  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
