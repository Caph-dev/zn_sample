from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.parse_metrics import female_ratio, parse_percent  # noqa: E402


class ParsePercentTests(unittest.TestCase):
    def test_explicit_percent_below_one_is_not_scaled_again(self) -> None:
        self.assertEqual(parse_percent("0.84%"), 0.84)

    def test_explicit_percent_above_one_is_preserved(self) -> None:
        self.assertEqual(parse_percent("97.33%"), 97.33)

    def test_decimal_ratio_without_percent_is_scaled(self) -> None:
        self.assertAlmostEqual(parse_percent("0.9733") or 0, 97.33)
        self.assertAlmostEqual(parse_percent(0.9733) or 0, 97.33)

    def test_already_normalized_number_is_preserved(self) -> None:
        self.assertEqual(parse_percent(97.33), 97.33)


class FemaleRatioTests(unittest.TestCase):
    def test_female_entry_is_used_directly(self) -> None:
        self.assertAlmostEqual(
            female_ratio([{"key": "Female", "value": "0.7027579"}]) or 0,
            70.27579,
        )

    def test_percent_value_is_not_scaled_again(self) -> None:
        self.assertAlmostEqual(
            female_ratio([{"key": "Female", "value": "70.28"}]) or 0,
            70.28,
        )

    def test_male_only_entry_is_inverted(self) -> None:
        """列表常只返回占比最高的单一性别；Male 单条时女性占比 = 1 - male。"""
        self.assertAlmostEqual(
            female_ratio([{"key": "Male", "value": "0.3325752"}]) or 0,
            66.74248,
        )
        self.assertAlmostEqual(
            female_ratio([{"key": "Male", "value": "33.25752"}]) or 0,
            66.74248,
        )

    def test_unknown_gender_key_is_not_guessed(self) -> None:
        self.assertIsNone(female_ratio([{"key": "Other", "value": "0.5"}]))
        self.assertIsNone(female_ratio([]))
        self.assertIsNone(female_ratio(None))


if __name__ == "__main__":
    unittest.main()
