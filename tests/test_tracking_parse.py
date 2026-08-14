from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.message_templates import tracking_message  # noqa: E402
from lib.tracking_parse import (  # noqa: E402
    normalize_tracking,
    tracking_numbers_equivalent,
)


class TrackingParseTests(unittest.TestCase):
    def test_9200_numbers_use_cbt_display_format(self) -> None:
        parsed = normalize_tracking("9200190412726311129185")
        self.assertEqual(parsed["tracking_no"], "9200190412726311129185")
        self.assertEqual(parsed["tracking_raw"], "CBT, 9200190412726311129185")

    def test_existing_cbt_prefix_stays_canonical(self) -> None:
        parsed = normalize_tracking("CBT,9200190412726311129185")
        self.assertEqual(parsed["tracking_raw"], "CBT, 9200190412726311129185")

    def test_prefixed_and_bare_numbers_are_equivalent(self) -> None:
        self.assertTrue(
            tracking_numbers_equivalent(
                "CBT, 9200190412726311129185",
                "9200190412726311129185",
            )
        )

    def test_tracking_message_keeps_cbt_prefix(self) -> None:
        body = tracking_message("en", "CBT, 9200190412726311129185")
        self.assertEqual(
            body,
            "Dear, here is the tracking number:CBT, 9200190412726311129185",
        )


if __name__ == "__main__":
    unittest.main()
