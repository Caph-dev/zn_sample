from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant import paths


class UserPathTests(unittest.TestCase):
    def test_ensure_user_dirs_creates_only_expected_children(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            application_directory = Path(temporary_directory) / "assistant-data"
            with patch("assistant.paths.user_data_dir", return_value=application_directory):
                self.assertEqual(paths.ensure_user_dirs(), application_directory)
                self.assertEqual(paths.database_path(), application_directory / "assistant.sqlite3")
                self.assertEqual(paths.runtime_dir(), application_directory / "runtime")

            for child in paths.USER_SUBDIRECTORIES:
                self.assertTrue((application_directory / child).is_dir())

    def test_platform_paths_use_standard_locations(self) -> None:
        with patch("assistant.paths.Path.home", return_value=Path("/home/operator")):
            with patch("assistant.paths.sys.platform", "darwin"):
                self.assertEqual(
                    paths.user_data_dir(),
                    Path("/home/operator/Library/Application Support/ZnSampleAssistant"),
                )
            with patch("assistant.paths.sys.platform", "linux"):
                self.assertEqual(
                    paths.user_data_dir(),
                    Path("/home/operator/.local/share/ZnSampleAssistant"),
                )


if __name__ == "__main__":
    unittest.main()
