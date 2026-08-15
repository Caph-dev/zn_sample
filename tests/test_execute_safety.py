from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from screen_sample_requests import (  # noqa: E402
    _run_execute_pipeline,
    _select_confirm_candidates_from_export,
    _select_execute_candidates_from_export,
    decide_api_approval_outcome,
)


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
            write_source="dom",
        )

        click_approve.assert_not_called()
        self.assertEqual(candidate["approve_status"], "skipped")
        self.assertEqual(candidate["action"], "skipped-api-preflight-state")

    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.ensure_store_exec_ready")
    @patch("screen_sample_requests.check_pending_application_api")
    @patch("screen_sample_requests.click_approve_for_apply_id")
    def test_dom_click_accepted_does_not_wait_for_list_refresh(
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
            write_source="dom",
        )

        ensure_ready.assert_called_once()
        click_approve.assert_called_once()
        self.assertEqual(candidate["approve_status"], "approved")
        self.assertEqual(candidate["action"], "approved")
        self.assertEqual(candidate["approve_confirmation"], "deferred")

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
            write_source="dom",
        )

        ensure_ready.assert_called_once()
        click_approve.assert_called_once()
        self.assertEqual(candidate["approve_status"], "failed")
        self.assertEqual(candidate["action"], "approve-failed")

    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.ensure_store_exec_ready")
    @patch("screen_sample_requests.confirm_application_approved_api")
    @patch("screen_sample_requests.check_pending_application_api")
    @patch("screen_sample_requests.approve_application_api")
    def test_failed_api_approval_stops_without_trying_next_candidate(
        self,
        approve_api,
        check_pending,
        confirm_approved,
        ensure_ready,
        resolve_product,
    ) -> None:
        resolve_product.return_value = {
            "ok": True,
            "sku": "sku-test",
            "option": "sku-test",
        }
        check_pending.return_value = {
            "ok": True,
            "state": "pending-approvable",
            "curr_status": 10,
        }
        approve_api.side_effect = RuntimeError(
            "批准 API 只接受待审核状态 status_type=10/11，实际为 10"
        )
        first = build_candidate()
        second = build_candidate()
        second["apply_id"] = "apply-test-002"
        second["creator_name"] = "creator_test_2"

        _run_execute_pipeline(
            store_id="store-test",
            candidates=[first, second],
            hero_data={"rows": []},
            write_feishu=False,
            execute_limit=1,
            execute_delay=0,
            config_path=None,
            page_wait=0,
            observe_approve_network=False,
            write_source="api",
        )

        approve_api.assert_called_once()
        confirm_approved.assert_not_called()
        self.assertEqual(check_pending.call_count, 2)
        self.assertEqual(first["approve_status"], "failed")
        self.assertNotIn("approve_status", second)

    def test_from_export_skips_approved_and_unknown_rows(self) -> None:
        rows = [
            {"eligible": False, "apply_id": "skip-fail", "creator_name": "a"},
            {
                "eligible": True,
                "apply_id": "already-approved",
                "approve_status": "approved",
                "creator_name": "b",
            },
            {
                "eligible": True,
                "apply_id": "unknown-status",
                "approve_status": "unknown",
                "creator_name": "c",
            },
            {
                "eligible": True,
                "apply_id": "ready-to-approve",
                "approve_status": None,
                "creator_name": "d",
            },
            {
                "eligible": True,
                "apply_id": "skipped-limit",
                "approve_status": "skipped",
                "creator_name": "e",
            },
        ]
        candidates = _select_execute_candidates_from_export(rows)
        self.assertEqual(
            [row["apply_id"] for row in candidates],
            ["ready-to-approve", "skipped-limit"],
        )

    def test_api_write_accepted_is_approved_without_ready_to_ship(self) -> None:
        outcome = decide_api_approval_outcome(
            approve_result={"ok": True, "state": "action-accepted"},
            pending_recheck=None,
        )
        self.assertEqual(outcome["approve_status"], "approved")
        self.assertEqual(outcome["approve_confirmation"], "deferred")
        self.assertFalse(outcome["stop_round"])

    def test_api_write_failed_and_still_pending_is_failed(self) -> None:
        outcome = decide_api_approval_outcome(
            approve_result={"ok": False, "state": "action-failed"},
            pending_recheck={"state": "pending-approvable"},
        )
        self.assertEqual(outcome["approve_status"], "failed")
        self.assertTrue(outcome["stop_round"])

    def test_confirm_export_waits_for_platform_lag(self) -> None:
        now = datetime(2026, 8, 15, 10, 20, 0)
        rows = [
            {
                "apply_id": "too-soon",
                "approve_status": "approved",
                "approved_at": "2026-08-15T10:15:00",
            },
            {
                "apply_id": "ready",
                "approve_status": "unknown",
                "approved_at": "2026-08-15T10:05:00",
            },
            {
                "apply_id": "already-confirmed",
                "approve_status": "approved",
                "approve_confirmation": "confirmed",
                "approved_at": "2026-08-15T10:00:00",
            },
        ]
        candidates = _select_confirm_candidates_from_export(rows, now=now)
        self.assertEqual([row["apply_id"] for row in candidates], ["ready"])
        self.assertEqual(rows[0]["approve_confirmation"], "waiting-platform-lag")

    def test_confirm_export_includes_skipped_not_found_after_prior_approval(self) -> None:
        rows = [
            {
                "apply_id": "varela-skip",
                "approve_status": "skipped",
                "action": "skipped-api-preflight-state",
                "approve_error": "批准前状态不再可批准: not-found-in-pending",
            },
            {
                "apply_id": "true-skip",
                "approve_status": "skipped",
                "action": "skipped-cannot-approve",
                "approve_error": "can_be_approved=false",
            },
            {
                "apply_id": "already-written",
                "approve_status": "unknown",
                "feishu_status": "created",
            },
        ]
        candidates = _select_confirm_candidates_from_export(rows, force=True)
        self.assertEqual([row["apply_id"] for row in candidates], ["varela-skip"])


if __name__ == "__main__":
    unittest.main()
