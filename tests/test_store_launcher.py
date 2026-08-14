from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.store_launcher import ensure_sample_store_open  # noqa: E402


class StoreLauncherTests(unittest.TestCase):
    def test_already_running_target_is_never_reopened(self) -> None:
        open_store = Mock()
        probe_store_page = Mock(
            return_value={"href": "https://seller-us.tiktok.com/homepage", "ready": "complete"}
        )

        result = ensure_sample_store_open(
            "store-two",
            wait_seconds=0,
            open_store_fn=open_store,
            list_running_stores_fn=lambda: [
                {"storeId": "store-two", "storeName": "二号店"}
            ],
            probe_store_page_fn=probe_store_page,
        )

        open_store.assert_not_called()
        probe_store_page.assert_called_once_with("store-two", retries=4)
        self.assertTrue(result["already_running"])
        self.assertFalse(result["opened_now"])

    def test_opens_target_when_no_store_is_running(self) -> None:
        open_store = Mock(return_value={"opened": True})

        result = ensure_sample_store_open(
            "store-two",
            wait_seconds=0,
            open_store_fn=open_store,
            list_running_stores_fn=lambda: [],
            probe_store_page_fn=lambda store_id, **kwargs: {
                "href": "about:blank",
                "ready": "complete",
            },
        )

        open_store.assert_called_once_with("store-two")
        self.assertTrue(result["opened_now"])
        self.assertFalse(result["already_running"])

    def test_refuses_to_close_or_switch_another_running_store(self) -> None:
        open_store = Mock()

        with self.assertRaisesRegex(RuntimeError, "不会自动关闭或切换"):
            ensure_sample_store_open(
                "store-two",
                wait_seconds=0,
                open_store_fn=open_store,
                list_running_stores_fn=lambda: [
                    {"storeId": "store-one", "storeName": "一号店"}
                ],
                probe_store_page_fn=Mock(),
            )

        open_store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
