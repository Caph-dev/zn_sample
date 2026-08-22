from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.domain.shipment_status import normalize_shipment_status


class ShipmentStatusTests(unittest.TestCase):
    def category_for(self, status_label: str | None) -> str:
        result = normalize_shipment_status(status_label=status_label, delivered_at=None)
        return result["status_category"]

    def test_normalizes_known_english_statuses(self) -> None:
        expected_categories = {
            "label created": "label_created",
            "Packed": "label_created",
            "Order placed": "label_created",
            "In transit": "in_transit",
            "Out for delivery": "out_for_delivery",
            "Delivered": "delivered",
            "Delivery canceled": "exception",
            "Couldn't deliver": "exception",
            "Return received": "returned",
        }
        for status_label, expected_category in expected_categories.items():
            with self.subTest(status_label=status_label):
                self.assertEqual(self.category_for(status_label), expected_category)

    def test_does_not_treat_shipped_or_negative_delivery_as_delivered(self) -> None:
        self.assertEqual(self.category_for("已发货"), "unknown")
        self.assertEqual(self.category_for("shipped"), "unknown")
        self.assertEqual(self.category_for("not delivered"), "unknown")

    def test_delivery_label_without_time_requires_confirmation(self) -> None:
        result = normalize_shipment_status(status_label="已送达", delivered_at=None)
        self.assertTrue(result["needs_delivery_time_confirmation"])
        self.assertIsNone(result["delivered_at"])

    def test_delivery_time_has_highest_priority(self) -> None:
        delivered_at = datetime(2026, 8, 22, 10, 30)
        result = normalize_shipment_status(status_label=None, delivered_at=delivered_at)
        self.assertEqual(result["status_category"], "delivered")
        self.assertIs(result["delivered_at"], delivered_at)
        self.assertFalse(result["needs_delivery_time_confirmation"])


if __name__ == "__main__":
    unittest.main()
