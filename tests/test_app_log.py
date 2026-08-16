from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.app_log import configure_logging  # noqa: E402
from lib.zclaw_cli import fs_path  # noqa: E402


class AppLogTests(unittest.TestCase):
    def tearDown(self) -> None:
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_configure_logging_uses_plain_message(self) -> None:
        configure_logging()
        root = logging.getLogger()
        self.assertTrue(root.handlers)
        formatter = root.handlers[0].formatter
        assert formatter is not None
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="只出名单",
            args=(),
            exc_info=None,
        )
        self.assertEqual(formatter.format(record), "只出名单")


class FsPathTests(unittest.TestCase):
    def test_fs_path_is_absolute_posix(self) -> None:
        value = fs_path(PROJECT_ROOT / "scripts" / "launch_sample.py")
        self.assertTrue(value.startswith("/") or (len(value) > 2 and value[1] == ":"))
        self.assertNotIn("\\", value)
        self.assertEqual(Path(value), PROJECT_ROOT / "scripts" / "launch_sample.py")


if __name__ == "__main__":
    unittest.main()
