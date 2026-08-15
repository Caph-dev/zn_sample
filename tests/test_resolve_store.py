from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.zclaw import resolve_store_id  # noqa: E402

STORE_2 = {
    "storeId": "27506607043054",
    "storeName": "跨境2号店",
}


class ResolveStoreIdTests(unittest.TestCase):
    @patch("lib.zclaw.list_running_stores", return_value=[STORE_2])
    def test_uses_the_only_running_store(self, _running) -> None:
        sid = resolve_store_id(default_store_id="27437742526069")
        self.assertEqual(sid, "27506607043054")

    @patch("lib.zclaw.list_running_stores", return_value=[STORE_2])
    def test_from_seller_home_without_default_uses_unique_running(self, _running) -> None:
        sid = resolve_store_id(default_store_id=None)
        self.assertEqual(sid, "27506607043054")

    @patch("lib.zclaw.list_running_stores", return_value=[])
    def test_from_seller_home_without_unique_running_does_not_guess(self, _running) -> None:
        with self.assertRaisesRegex(RuntimeError, "无法解析 storeId"):
            resolve_store_id(default_store_id=None)


if __name__ == "__main__":
    unittest.main()
