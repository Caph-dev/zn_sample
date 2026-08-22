from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.domain.sku_images import (
    FOLLOWUP_IMAGE_PATH,
    FOLLOWUP_IMAGE_PRODUCT_ID,
    resolve_followup_attachment,
)
from scripts.lib.filters import ACTIVE_HERO_PRODUCT_ID


class FollowupSkuImageTests(unittest.TestCase):
    def test_product_id_constant_is_shared_with_screening(self) -> None:
        self.assertIs(FOLLOWUP_IMAGE_PRODUCT_ID, ACTIVE_HERO_PRODUCT_ID)

    def test_only_exact_active_product_id_resolves_attachment(self) -> None:
        result = resolve_followup_attachment(
            product_id=ACTIVE_HERO_PRODUCT_ID,
            resolved_sku="B006-A",
        )
        self.assertEqual(
            result,
            {"matched": True, "sku_key": "b005", "path": FOLLOWUP_IMAGE_PATH},
        )

    def test_sku_text_and_other_product_ids_do_not_match(self) -> None:
        nonmatching_inputs = (
            {"product_id": "9999999999999999999", "resolved_sku": "B005"},
            {"product_id": None, "resolved_sku": "b005"},
            {"product_id": "B006-A", "resolved_sku": "B006-A"},
        )
        for input_values in nonmatching_inputs:
            with self.subTest(input_values=input_values):
                self.assertEqual(
                    resolve_followup_attachment(**input_values),
                    {"matched": False, "sku_key": "", "path": ""},
                )


if __name__ == "__main__":
    unittest.main()
