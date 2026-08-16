from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from send_sample_intro import _filter_targets  # noqa: E402


class FilterTargetsTests(unittest.TestCase):
    def test_from_export_keeps_creator_id_when_filtering_by_name(self) -> None:
        rows = [
            {
                "creator_name": "rayma2691",
                "creator_id": "cid-1",
                "apply_id": "a1",
            },
            {
                "creator_name": "dani_lynn_colors",
                "creator_id": "7496160321889208868",
                "apply_id": "a2",
                "eligible": True,
            },
        ]
        picked = _filter_targets(rows, creator_name="dani_lynn_colors")
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["creator_id"], "7496160321889208868")
        self.assertEqual(picked[0]["apply_id"], "a2")

    def test_filter_is_case_insensitive_on_handle(self) -> None:
        rows = [{"creator_name": "Dani_Lynn_Colors", "creator_id": "cid"}]
        picked = _filter_targets(rows, creator_name="dani_lynn_colors")
        self.assertEqual(len(picked), 1)

    def test_missing_name_returns_empty(self) -> None:
        picked = _filter_targets(
            [{"creator_name": "other", "creator_id": "cid"}],
            creator_name="dani_lynn_colors",
        )
        self.assertEqual(picked, [])


if __name__ == "__main__":
    unittest.main()
