from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.message_templates import tracking_message  # noqa: E402
from lib.tracking_parse import (  # noqa: E402
    normalize_tracking,
    parse_tiktok_logistics,
    tracking_numbers_equivalent,
)


class TrackingParseTests(unittest.TestCase):
    def test_bare_9200_number_does_not_invent_cbt_prefix(self) -> None:
        parsed = normalize_tracking("9200190412726311129185")
        self.assertEqual(parsed["tracking_no"], "9200190412726311129185")
        self.assertEqual(parsed["tracking_raw"], "9200190412726311129185")

    def test_keeps_whatever_carrier_prefix_is_already_present(self) -> None:
        parsed = normalize_tracking("USPS,9200190412726311129185")
        self.assertEqual(parsed["tracking_raw"], "USPS, 9200190412726311129185")

    def test_uses_explicit_carrier_argument(self) -> None:
        parsed = normalize_tracking(
            "9200190412726311129185",
            carrier="CBT",
        )
        self.assertEqual(parsed["tracking_raw"], "CBT, 9200190412726311129185")

    def test_prefixed_and_bare_numbers_are_equivalent(self) -> None:
        self.assertTrue(
            tracking_numbers_equivalent(
                "CBT, 9200190412726311129185",
                "9200190412726311129185",
            )
        )
        self.assertTrue(
            tracking_numbers_equivalent(
                "USPS, 9200190412726311129185",
                "9200190412726311129185",
            )
        )

    def test_page_label_keeps_non_cbt_prefix(self) -> None:
        parsed = parse_tiktok_logistics(
            "TikTok 物流 USPS, 9200190412726311129185",
            order_id="577524102614586321",
        )
        self.assertEqual(parsed["tracking_raw"], "USPS, 9200190412726311129185")
        self.assertEqual(parsed["via"], "label")

    def test_tracking_message_keeps_given_prefix(self) -> None:
        body = tracking_message("en", "USPS, 9200190412726311129185")
        self.assertEqual(
            body,
            "Dear, here is the tracking number:USPS, 9200190412726311129185",
        )


if __name__ == "__main__":
    unittest.main()
