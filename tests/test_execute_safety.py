from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from screen_sample_requests import _run_execute_pipeline  # noqa: E402


def build_candidate() -> dict:
    return {
        "apply_id": "apply-test-001",
        "creator_id": "creator-test-001",
        "creator_name": "creator_test",
        "product_id": "product-test-001",
        "can_be_approved": True,
        "eligible": True,
    }


class ExecuteSafetyTests(unittest.TestCase):
    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.check_pending_application_api")
    @patch("screen_sample_requests.click_approve_for_apply_id")
    def test_preflight_state_blocks_dom_approval(
        self,
        click_approve,
        check_pending,
        resolve_product,
    ) -> None:
        resolve_product.return_value = {
            "ok": True,
            "sku": "sku-test",
            "option": "sku-test",
        }
        check_pending.return_value = {
            "ok": True,
            "state": "not-found-in-pending",
        }
        candidate = build_candidate()

        _run_execute_pipeline(
            store_id="store-test",
            candidates=[candidate],
            hero_data={"rows": []},
            write_feishu=False,
            execute_limit=1,
            execute_delay=0,
            config_path=None,
            page_wait=0,
            observe_approve_network=False,
        )

        click_approve.assert_not_called()
        self.assertEqual(candidate["approve_status"], "skipped")
        self.assertEqual(candidate["action"], "skipped-api-preflight-state")

    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.ensure_store_exec_ready")
    @patch("screen_sample_requests.check_pending_application_api")
    @patch("screen_sample_requests.click_approve_for_apply_id")
    def test_unknown_postcheck_stops_without_confirming_approval(
        self,
        click_approve,
        check_pending,
        ensure_ready,
        resolve_product,
    ) -> None:
        resolve_product.return_value = {
            "ok": True,
            "sku": "sku-test",
            "option": "sku-test",
        }
        check_pending.side_effect = [
            {"ok": True, "state": "pending-approvable"},
            RuntimeError("synthetic postcheck failure"),
            RuntimeError("synthetic postcheck failure"),
        ]
        click_approve.return_value = {"ok": True, "approved": True, "steps": []}
        candidate = build_candidate()

        _run_execute_pipeline(
            store_id="store-test",
            candidates=[candidate],
            hero_data={"rows": []},
            write_feishu=False,
            execute_limit=1,
            execute_delay=0,
            config_path=None,
            page_wait=0,
            observe_approve_network=False,
        )

        ensure_ready.assert_called_once()
        click_approve.assert_called_once()
        self.assertEqual(candidate["approve_status"], "unknown")
        self.assertEqual(candidate["action"], "approve-unknown")
        self.assertEqual(candidate["approve_postcheck"]["state"], "verification-error")

    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.ensure_store_exec_ready")
    @patch("screen_sample_requests.check_pending_application_api")
    @patch("screen_sample_requests.click_approve_for_apply_id")
    def test_dom_exception_is_failed_only_after_api_confirms_still_pending(
        self,
        click_approve,
        check_pending,
        ensure_ready,
        resolve_product,
    ) -> None:
        resolve_product.return_value = {
            "ok": True,
            "sku": "sku-test",
            "option": "sku-test",
        }
        check_pending.side_effect = [
            {"ok": True, "state": "pending-approvable"},
            {"ok": True, "state": "pending-approvable"},
            {"ok": True, "state": "pending-approvable"},
        ]
        click_approve.side_effect = RuntimeError("synthetic DOM failure")
        candidate = build_candidate()

        _run_execute_pipeline(
            store_id="store-test",
            candidates=[candidate],
            hero_data={"rows": []},
            write_feishu=False,
            execute_limit=1,
            execute_delay=0,
            config_path=None,
            page_wait=0,
            observe_approve_network=False,
        )

        ensure_ready.assert_called_once()
        click_approve.assert_called_once()
        self.assertEqual(candidate["approve_status"], "failed")
        self.assertEqual(candidate["action"], "approve-failed")


if __name__ == "__main__":
    unittest.main()
