from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from send_sample_intro import (  # noqa: E402
    _filter_targets,
    _intro_target_key,
    _load_sent_intro_audit,
    _target_product_key,
)


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


class IntroDedupeKeyTests(unittest.TestCase):
    def test_key_uses_creator_and_product_not_creator_only(self) -> None:
        product_one = {
            "creator_id": "creator-1",
            "sample_product_option": "328",
            "product_id": "product-a",
        }
        product_two = {
            "creator_id": "creator-1",
            "sample_product_option": "P002",
            "product_id": "product-b",
        }

        self.assertEqual(_target_product_key(product_one), "328")
        self.assertEqual(_intro_target_key(product_one), ("creator-1", "328"))
        self.assertNotEqual(_intro_target_key(product_one), _intro_target_key(product_two))

    def test_load_sent_audit_keeps_same_application_fallback(self) -> None:
        with patch("send_sample_intro.ROOT") as project_root:
            export_path = project_root.__truediv__.return_value.__truediv__.return_value
            export_path.glob.return_value = []
            sent_keys, sent_apply_ids = _load_sent_intro_audit()

        self.assertEqual(sent_keys, set())
        self.assertEqual(sent_apply_ids, set())


if __name__ == "__main__":
    unittest.main()
