from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.parse_metrics import parse_percent  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
