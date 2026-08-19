from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.store_launcher import (  # noqa: E402
    DEFAULT_PREPARE_STORE_ID,
    ensure_sample_store_open,
    prepare_sample_store_debug,
    resolve_store_id_for_prepare,
)


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


class ResolveStoreIdForPrepareTests(unittest.TestCase):
    def test_uses_unique_running(self) -> None:
        sid = resolve_store_id_for_prepare(
            list_running_stores_fn=lambda: [
                {"storeId": "sid-1", "storeName": "一号店"}
            ],
            list_all_stores_fn=Mock(side_effect=AssertionError),
        )
        self.assertEqual(sid, "sid-1")

    def test_explicit_id_wins(self) -> None:
        sid = resolve_store_id_for_prepare(
            store_id="sid-9",
            list_running_stores_fn=Mock(side_effect=AssertionError),
            list_all_stores_fn=Mock(side_effect=AssertionError),
        )
        self.assertEqual(sid, "sid-9")

    def test_zero_running_defaults_to_store_two(self) -> None:
        sid = resolve_store_id_for_prepare(
            list_running_stores_fn=lambda: [],
            list_all_stores_fn=lambda limit=50: [
                {"storeId": "sid-3", "storeName": "唯一店"},
                {"storeId": DEFAULT_PREPARE_STORE_ID, "storeName": "跨境2号店"},
            ],
        )
        self.assertEqual(sid, DEFAULT_PREPARE_STORE_ID)

    def test_zero_running_many_listed_defaults_to_store_two(self) -> None:
        sid = resolve_store_id_for_prepare(
            list_running_stores_fn=lambda: [],
            list_all_stores_fn=lambda limit=50: [
                {"storeId": "a", "storeName": "甲"},
                {"storeId": DEFAULT_PREPARE_STORE_ID, "storeName": "跨境2号店"},
                {"storeId": "b", "storeName": "乙"},
            ],
        )
        self.assertEqual(sid, DEFAULT_PREPARE_STORE_ID)

    def test_zero_running_without_default_does_not_guess(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "无法唯一确定"):
            resolve_store_id_for_prepare(
                default_store_id=None,
                list_running_stores_fn=lambda: [],
                list_all_stores_fn=lambda limit=50: [
                    {"storeId": "a", "storeName": "甲"},
                    {"storeId": "b", "storeName": "乙"},
                ],
            )

    def test_zero_running_empty_list_still_defaults_to_store_two(self) -> None:
        sid = resolve_store_id_for_prepare(
            list_running_stores_fn=lambda: [],
            list_all_stores_fn=lambda limit=50: [],
        )
        self.assertEqual(sid, DEFAULT_PREPARE_STORE_ID)

    def test_zero_running_errors_if_default_store_missing_from_account(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "找不到默认的"):
            resolve_store_id_for_prepare(
                list_running_stores_fn=lambda: [],
                list_all_stores_fn=lambda limit=50: [
                    {"storeId": "a", "storeName": "甲"},
                    {"storeId": "b", "storeName": "乙"},
                ],
            )

    def test_store_name_matches_list_when_nothing_running(self) -> None:
        sid = resolve_store_id_for_prepare(
            store_name="乙店",
            list_running_stores_fn=lambda: [],
            list_all_stores_fn=lambda limit=50: [
                {"storeId": "a", "storeName": "甲店"},
                {"storeId": "b", "storeName": "乙店"},
            ],
        )
        self.assertEqual(sid, "b")


class PrepareSampleStoreDebugTests(unittest.TestCase):
    def test_reopens_running_target_with_close_then_open(self) -> None:
        order: list[str] = []
        running = [{"storeId": "sid-1", "storeName": "一号店"}]

        def list_running() -> list[dict]:
            return list(running)

        def close_store(store_id: str) -> dict:
            order.append(f"close:{store_id}")
            running.clear()
            return {"closed": True}

        def open_store(store_id: str, *, timeout: int = 180) -> dict:
            order.append(f"open:{store_id}:{timeout}")
            running.append({"storeId": store_id, "storeName": "一号店"})
            return {"opened": True, "debugPort": 9333}

        probe = Mock(return_value={"href": "about:blank", "ready": "complete"})

        result = prepare_sample_store_debug(
            "sid-1",
            wait_seconds=0,
            close_settle_seconds=0,
            close_wait_seconds=1,
            probe_wait_seconds=1,
            open_store_timeout=300,
            open_store_fn=open_store,
            close_store_fn=close_store,
            list_running_stores_fn=list_running,
            probe_store_page_fn=probe,
            sleep_fn=lambda _s: None,
        )

        self.assertEqual(order, ["close:sid-1", "open:sid-1:300"])
        self.assertTrue(result["reopened"])
        self.assertTrue(result["opened_now"])
        self.assertFalse(result["already_running"])
        probe.assert_called()
        self.assertEqual(probe.call_args.kwargs.get("retries"), 0)

    def test_opens_when_nothing_is_running(self) -> None:
        close_store = Mock(side_effect=AssertionError)
        open_store = Mock(return_value={"opened": True})

        result = prepare_sample_store_debug(
            "sid-2",
            wait_seconds=0,
            close_settle_seconds=0,
            probe_wait_seconds=1,
            open_store_fn=open_store,
            close_store_fn=close_store,
            list_running_stores_fn=lambda: [],
            probe_store_page_fn=lambda store_id, **kwargs: {
                "href": "about:blank",
                "ready": "complete",
            },
            sleep_fn=lambda _s: None,
        )

        close_store.assert_not_called()
        open_store.assert_called_once()
        self.assertEqual(open_store.call_args.args[0], "sid-2")
        self.assertFalse(result["reopened"])
        self.assertTrue(result["opened_now"])

    def test_refuses_other_running_store(self) -> None:
        open_store = Mock()
        close_store = Mock()
        with self.assertRaisesRegex(RuntimeError, "不会自动关闭或切换"):
            prepare_sample_store_debug(
                "sid-2",
                wait_seconds=0,
                open_store_fn=open_store,
                close_store_fn=close_store,
                list_running_stores_fn=lambda: [
                    {"storeId": "sid-1", "storeName": "一号店"}
                ],
                probe_store_page_fn=Mock(side_effect=AssertionError),
            )
        open_store.assert_not_called()
        close_store.assert_not_called()

    def test_probe_retries_until_ready(self) -> None:
        attempts = {"n": 0}

        def probe(store_id: str, **kwargs) -> dict:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("not ready")
            return {"href": "https://seller.example/home", "ready": "complete"}

        clock = {"t": 0.0}

        result = prepare_sample_store_debug(
            "sid-1",
            wait_seconds=0,
            close_settle_seconds=0,
            probe_wait_seconds=20,
            open_store_fn=lambda store_id, **kwargs: {"opened": True},
            close_store_fn=Mock(side_effect=AssertionError),
            list_running_stores_fn=lambda: [],
            probe_store_page_fn=probe,
            sleep_fn=lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
            clock_fn=lambda: clock["t"],
        )
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(result["probe"]["ready"], "complete")


if __name__ == "__main__":
    unittest.main()
