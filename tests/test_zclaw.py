from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.zclaw import is_timeout_expired_error, zclaw_exec  # noqa: E402
from lib.page_api import (  # noqa: E402
    AffiliatePageContext,
    get_seller_read_json,
    post_sample_group_action_json,
    SellerPageContext,
)


def _ok_outer(payload: str = "arrived") -> dict:
    return {
        "ok": True,
        "data": {"data": {"result": f'"{payload}"'}},
    }


class ZclawExecRetryTests(unittest.TestCase):
    def test_retries_timeout_expired_when_explicitly_enabled(self) -> None:
        outer = Mock(
            side_effect=[
                subprocess.TimeoutExpired("ziniao-cli", 2),
                subprocess.TimeoutExpired("ziniao-cli", 2),
                _ok_outer(),
            ]
        )
        with (
            patch("lib.zclaw.zclaw_invoke", outer),
            patch("lib.zclaw.time.sleep") as sleep,
        ):
            result = zclaw_exec(
                "store",
                "script",
                timeout=2,
                retries=2,
                retry_base_sec=0.5,
                retry_timeout_expired=True,
                operation="href_probe",
            )
        self.assertEqual(result, "arrived")
        self.assertEqual(outer.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_consecutive_timeouts_stop_at_max_attempts(self) -> None:
        outer = Mock(side_effect=subprocess.TimeoutExpired("ziniao-cli", 2))
        with (
            patch("lib.zclaw.zclaw_invoke", outer),
            patch("lib.zclaw.time.sleep"),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                zclaw_exec(
                    "store",
                    "script",
                    timeout=2,
                    retries=2,
                    retry_timeout_expired=True,
                )
        self.assertEqual(outer.call_count, 3)

    def test_retries_zero_does_not_sleep(self) -> None:
        outer = Mock(side_effect=subprocess.TimeoutExpired("ziniao-cli", 2))
        with (
            patch("lib.zclaw.zclaw_invoke", outer),
            patch("lib.zclaw.time.sleep") as sleep,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                zclaw_exec(
                    "store",
                    "script",
                    timeout=2,
                    retries=0,
                    retry_timeout_expired=True,
                )
        sleep.assert_not_called()
        self.assertEqual(outer.call_count, 1)

    def test_timeout_not_retried_without_explicit_opt_in(self) -> None:
        outer = Mock(side_effect=subprocess.TimeoutExpired("ziniao-cli", 2))
        with (
            patch("lib.zclaw.zclaw_invoke", outer),
            patch("lib.zclaw.time.sleep") as sleep,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                zclaw_exec("store", "script", timeout=2, retries=4)
        self.assertEqual(outer.call_count, 1)
        sleep.assert_not_called()

    def test_plain_error_is_not_retried(self) -> None:
        outer = Mock(side_effect=ValueError("boom"))
        with (
            patch("lib.zclaw.zclaw_invoke", outer),
            patch("lib.zclaw.time.sleep") as sleep,
        ):
            with self.assertRaises(ValueError):
                zclaw_exec(
                    "store",
                    "script",
                    timeout=2,
                    retries=4,
                    retry_timeout_expired=True,
                )
        self.assertEqual(outer.call_count, 1)
        sleep.assert_not_called()

    def test_deadline_blocks_retry_when_insufficient_budget(self) -> None:
        outer = Mock(side_effect=subprocess.TimeoutExpired("ziniao-cli", 2))
        with (
            patch("lib.zclaw.zclaw_invoke", outer),
            patch("lib.zclaw.time.sleep") as sleep,
            patch(
                "lib.time_budget.time.monotonic",
                side_effect=[1000.0, 1001.0, 1001.9],
            ),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                zclaw_exec(
                    "store",
                    "script",
                    timeout=2,
                    retries=2,
                    retry_timeout_expired=True,
                    deadline=1001.5,
                )
        # 首次超时后剩余预算不足退避 0.5s → 不再重试。
        self.assertEqual(outer.call_count, 1)
        sleep.assert_not_called()

    def test_retry_event_log_is_structured_and_redacts_script(self) -> None:
        outer = Mock(
            side_effect=[
                subprocess.TimeoutExpired("ziniao-cli", 2),
                _ok_outer(),
            ]
        )
        secret = "SECRET-COOKIE-TOKEN-123456"
        with (
            patch("lib.zclaw.zclaw_invoke", outer),
            patch("lib.zclaw.time.sleep"),
            self.assertLogs("lib.zclaw", level="INFO") as captured,
        ):
            zclaw_exec(
                "store",
                f"script {secret}",
                timeout=2,
                retries=1,
                retry_timeout_expired=True,
                retry_base_sec=0.5,
                operation="href_probe",
            )
        log_text = "\n".join(captured.output)
        self.assertIn("event=zclaw_execute_retry", log_text)
        self.assertIn("operation=href_probe", log_text)
        self.assertIn("attempt=1", log_text)
        self.assertIn("max_attempts=2", log_text)
        self.assertIn("timeout_seconds=2", log_text)
        self.assertIn("backoff_seconds=0.50", log_text)
        self.assertIn("error_type=TimeoutExpired", log_text)
        self.assertIn("elapsed_ms=", log_text)
        self.assertNotIn(secret, log_text)


class TimeoutErrorClassifierTests(unittest.TestCase):
    def test_classifies_subprocess_timeout_expired(self) -> None:
        self.assertTrue(
            is_timeout_expired_error(subprocess.TimeoutExpired("ziniao-cli", 2))
        )
        self.assertFalse(is_timeout_expired_error(ValueError("boom")))
        self.assertFalse(is_timeout_expired_error(RuntimeError("network")))


class WritePathRetrySafetyTests(unittest.TestCase):
    def test_write_action_keeps_timeout_retry_disabled(self) -> None:
        context = AffiliatePageContext(
            href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US",
            shop_id="shop",
            shop_region="US",
        )
        with patch("lib.page_api._post_page_json") as post:
            post_sample_group_action_json("store", {"action": 1}, context=context)
        self.assertFalse(post.call_args.kwargs.get("retry_timeout_expired", False))

    def test_read_get_enables_timeout_retry(self) -> None:
        context = SellerPageContext(
            href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            shop_id="shop",
            shop_region="US",
        )
        with patch("lib.page_api._post_page_json") as post:
            get_seller_read_json(
                "store",
                "/api/v1/fulfillment/na/logistic_detail/list",
                query={"main_order_id": "1"},
                context=context,
            )
        self.assertTrue(post.call_args.kwargs.get("retry_timeout_expired"))


if __name__ == "__main__":
    unittest.main()
