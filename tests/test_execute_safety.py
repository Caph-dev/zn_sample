from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from screen_sample_requests import (  # noqa: E402
    _backfill_feishu_order_no,
    _looks_like_tiktok_order_no,
    _reject_if_not_exact_hero,
    _run_confirm_pipeline,
    _run_execute_pipeline,
    _select_confirm_candidates_from_export,
    _select_execute_candidates_from_export,
    _select_feishu_reconcile_rows_from_export,
    _select_order_backfill_rows_from_export,
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
        "sales_eligible": True,
    }


class ExecuteSafetyTests(unittest.TestCase):
    # These regressions exercise the downstream platform gates after a valid
    # content proof; proof rejection itself is covered in test_screen_content_review.
    @patch("screen_sample_requests.validate_content_review", new=lambda row: (True, "valid test proof"))
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

    @patch("screen_sample_requests.validate_content_review", new=lambda row: (True, "valid test proof"))
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

        with self.assertLogs("screen_sample_requests", level="INFO") as captured_logs:
            _run_execute_pipeline(
                store_id="store-test",
                candidates=[candidate],
                hero_data={"rows": []},
                write_feishu=False,
                execute_limit=50,
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
        self.assertTrue(
            any("[批准] (1/1)" in message for message in captured_logs.output)
        )

    @patch("screen_sample_requests.validate_content_review", new=lambda row: (True, "valid test proof"))
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

    @patch("screen_sample_requests.validate_content_review", new=lambda row: (True, "valid test proof"))
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

    @patch("screen_sample_requests.validate_content_review", new=lambda row: (True, "valid test proof"))
    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.check_pending_application_api")
    @patch("screen_sample_requests.approve_application_api")
    def test_non_hero_product_is_not_approved(
        self,
        approve_api,
        check_pending,
        resolve_product,
    ) -> None:
        resolve_product.return_value = {
            "ok": True,
            "sku": "P001",
            "option": "P001",
        }
        candidate = build_candidate()
        candidate["product_id"] = "1732117543281857378"
        hero_data = {
            "hero_keys": {"328", "1732060411527205730"},
            "rows": [
                {
                    "sku": "328",
                    "is_hero": True,
                    "product_id": "1732060411527205730",
                },
                {
                    "sku": "P001",
                    "is_hero": False,
                    "product_id": "1732117543281857378",
                },
            ],
        }

        _run_execute_pipeline(
            store_id="store-test",
            candidates=[candidate],
            hero_data=hero_data,
            write_feishu=False,
            execute_limit=1,
            execute_delay=0,
            config_path=None,
            page_wait=0,
            observe_approve_network=False,
            write_source="api",
        )

        approve_api.assert_not_called()
        check_pending.assert_not_called()
        resolve_product.assert_not_called()
        self.assertEqual(candidate["approve_status"], "skipped")
        self.assertEqual(candidate["action"], "skipped-not-hero")
        self.assertIn("非主推款", candidate["approve_error"])

    def test_reject_helper_blocks_sku_substring_in_product_id(self) -> None:
        reason = _reject_if_not_exact_hero(
            {"product_id": "1732117543281857378"},
            {"hero_keys": {"328", "1732060411527205730"}},
        )
        self.assertIsNotNone(reason)
        self.assertIn("非主推款", str(reason))

    def test_reject_helper_blocks_hero_outside_active_allowlist(self) -> None:
        reason = _reject_if_not_exact_hero(
            {"product_id": "1732060411527205730"},
            {"hero_keys": {"1732060411527205730", "1732414717062320994"}},
            allowed_product_ids={"1732414717062320994"},
        )
        self.assertIsNotNone(reason)
        self.assertIn("非当前跟进款", str(reason))

    def test_reject_helper_allows_active_hero_product(self) -> None:
        reason = _reject_if_not_exact_hero(
            {"product_id": "1732414717062320994"},
            {"hero_keys": {"1732414717062320994"}},
            allowed_product_ids={"1732414717062320994"},
        )
        self.assertIsNone(reason)

    @patch("screen_sample_requests.validate_content_review", new=lambda row: (True, "valid test proof"))
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
        for row in rows:
            row["sales_eligible"] = row["eligible"]
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
        self.assertEqual(
            [row["apply_id"] for row in candidates],
            ["varela-skip", "already-written"],
        )

    def test_confirmed_row_without_feishu_record_is_recovery_candidate(self) -> None:
        rows = [
            {
                "apply_id": "recover-feishu",
                "approve_status": "approved",
                "approve_confirmation": "confirmed",
            },
            {
                "apply_id": "already-linked",
                "approve_status": "approved",
                "approve_confirmation": "confirmed",
                "feishu_record_id": "rec-linked",
                "feishu_status": "created",
            },
            {
                "apply_id": "blocked",
                "approve_status": "approved",
                "approve_confirmation": "confirmed",
                "feishu_relation_status": "blocked-product-unresolved",
            },
            {
                "apply_id": "uncertain-write",
                "approve_status": "approved",
                "approve_confirmation": "confirmed",
                "feishu_relation_status": "write-uncertain",
            },
        ]

        candidates = _select_feishu_reconcile_rows_from_export(rows)

        self.assertEqual([row["apply_id"] for row in candidates], ["recover-feishu"])

    def test_order_no_validation_accepts_only_real_order_ids(self) -> None:
        self.assertEqual(
            _looks_like_tiktok_order_no("577532709908747188"),
            "577532709908747188",
        )
        self.assertEqual(
            _looks_like_tiktok_order_no(" 577532709908747188 "),
            "577532709908747188",
        )
        self.assertEqual(_looks_like_tiktok_order_no("0"), "")
        self.assertEqual(_looks_like_tiktok_order_no(""), "")
        self.assertEqual(_looks_like_tiktok_order_no(None), "")
        self.assertEqual(_looks_like_tiktok_order_no("12345"), "")
        self.assertEqual(_looks_like_tiktok_order_no("577532709908747188abc"), "")

    def test_order_backfill_selection_requires_confirmed_linked_record(self) -> None:
        now = datetime(2026, 8, 15, 10, 20, 0)
        rows = [
            {
                "apply_id": "ready",
                "approve_status": "approved",
                "feishu_record_id": "rec-ready",
                "approve_confirmation": "confirmed",
                "approved_at": "2026-08-15T10:05:00",
            },
            {
                "apply_id": "not-confirmed",
                "approve_status": "approved",
                "feishu_record_id": "rec-not-confirmed",
                "approved_at": "2026-08-15T10:15:00",
            },
            {
                "apply_id": "no-record",
                "approve_status": "approved",
                "feishu_record_id": "",
                "approve_confirmation": "confirmed",
                "approved_at": "2026-08-15T10:00:00",
            },
            {
                "apply_id": "already-written",
                "approve_status": "approved",
                "feishu_record_id": "rec-written",
                "approve_confirmation": "confirmed",
                "feishu_order_status": "written",
                "approved_at": "2026-08-15T10:00:00",
            },
            {
                "apply_id": "write-uncertain",
                "approve_status": "approved",
                "feishu_record_id": "rec-uncertain",
                "approve_confirmation": "confirmed",
                "feishu_order_status": "write-uncertain",
                "approved_at": "2026-08-15T10:00:00",
            },
            {
                "apply_id": "invalid-order",
                "approve_status": "approved",
                "feishu_record_id": "rec-invalid-order",
                "approve_confirmation": "confirmed",
                "feishu_order_status": "skipped-invalid-order-no",
                "approved_at": "2026-08-15T10:00:00",
            },
            {
                "apply_id": "ambiguous-record",
                "approve_status": "approved",
                "feishu_record_id": "rec-ambiguous",
                "approve_confirmation": "confirmed",
                "feishu_relation_status": "ambiguous-match",
                "approved_at": "2026-08-15T10:00:00",
            },
        ]
        selected = _select_order_backfill_rows_from_export(rows, now=now)
        self.assertEqual([row["apply_id"] for row in selected], ["ready"])

    @patch("screen_sample_requests.update_record_order_no")
    def test_backfill_helper_records_existing_different_order(self, update_order) -> None:
        update_order.return_value = {
            "status": "skipped-existing-different",
            "current_order": "577599999999999999",
        }
        row: dict = {}
        _backfill_feishu_order_no(
            bitable_token="token-test",
            row=row,
            order_no="577532709908747188",
            record_id="rec-1",
            app_token="app-test",
            table_id="tbl-test",
        )
        self.assertEqual(row["feishu_order_status"], "skipped-existing-different")
        self.assertIn("不覆盖", row["feishu_order_error"])

    @patch("screen_sample_requests.update_record_order_no")
    def test_backfill_helper_records_post_write_uncertainty(self, update_order) -> None:
        update_order.return_value = {
            "status": "write-uncertain",
            "current_order": "577599999999999999",
            "verification_error": "写后读取的订单号与请求值不一致",
        }
        row: dict = {}

        _backfill_feishu_order_no(
            bitable_token="token-test",
            row=row,
            order_no="577532709908747188",
            record_id="rec-1",
            app_token="app-test",
            table_id="tbl-test",
        )

        self.assertEqual(row["feishu_order_status"], "write-uncertain")
        self.assertIn("写后核验不确定", row["feishu_order_error"])

    @patch("screen_sample_requests.update_record_order_no")
    @patch("screen_sample_requests.check_application_in_tab_api")
    @patch("screen_sample_requests.list_sample_product_options")
    @patch("screen_sample_requests.get_bitable_access_token")
    @patch("lib.app_config.load_bitable_settings")
    def test_order_backfill_pass_writes_only_order_field(
        self,
        load_settings,
        get_token,
        list_options,
        check_tab,
        update_order,
    ) -> None:
        load_settings.return_value = {"app_token": "app-test", "table_id": "tbl-test"}
        get_token.return_value = "token-test"
        list_options.return_value = ["328"]
        check_tab.return_value = {
            "ok": True,
            "state": "pending-not-approvable",
            "curr_status": 20,
            "main_order_id": "577532709908747188",
        }
        update_order.return_value = {
            "status": "written",
            "record_id": "rec-1",
        }
        row = {
            "apply_id": "apply-a1",
            "creator_id": "creator-1",
            "creator_name": "alice",
            "product_id": "product-1",
            "approve_status": "approved",
            "approve_confirmation": "confirmed",
            "feishu_record_id": "rec-1",
            "approved_at": "2026-08-15T10:00:00",
        }

        _run_confirm_pipeline(
            store_id="store-test",
            candidates=[],
            hero_data={"rows": []},
            write_feishu=True,
            execute_limit=1,
            config_path=None,
            order_backfill_rows=[row],
        )

        check_tab.assert_called_once()
        update_order.assert_called_once()
        self.assertEqual(update_order.call_args.args[2], "577532709908747188")
        self.assertEqual(update_order.call_args.args[1], "rec-1")
        self.assertEqual(row["feishu_order_status"], "written")
        self.assertEqual(row["order_no"], "577532709908747188")

    @patch("screen_sample_requests.update_record_order_no")
    @patch("screen_sample_requests.check_application_in_tab_api")
    @patch("screen_sample_requests.list_sample_product_options")
    @patch("screen_sample_requests.get_bitable_access_token")
    @patch("lib.app_config.load_bitable_settings")
    def test_order_backfill_pass_skips_invalid_order_no(
        self,
        load_settings,
        get_token,
        list_options,
        check_tab,
        update_order,
    ) -> None:
        load_settings.return_value = {"app_token": "app-test", "table_id": "tbl-test"}
        get_token.return_value = "token-test"
        list_options.return_value = ["328"]
        check_tab.return_value = {
            "ok": True,
            "state": "pending-not-approvable",
            "curr_status": 20,
            "main_order_id": "0",
        }
        row = {
            "apply_id": "apply-a1",
            "creator_id": "creator-1",
            "creator_name": "alice",
            "product_id": "product-1",
            "approve_status": "approved",
            "approve_confirmation": "confirmed",
            "feishu_record_id": "rec-1",
            "approved_at": "2026-08-15T10:00:00",
        }

        _run_confirm_pipeline(
            store_id="store-test",
            candidates=[],
            hero_data={"rows": []},
            write_feishu=True,
            execute_limit=1,
            config_path=None,
            order_backfill_rows=[row],
        )

        update_order.assert_not_called()
        self.assertEqual(row["feishu_order_status"], "skipped-invalid-order-no")

    @patch("screen_sample_requests.update_record_order_no")
    @patch("screen_sample_requests.create_creator_relation_record")
    @patch("screen_sample_requests.resolve_duplicate_record")
    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.confirm_application_approved_api")
    @patch("screen_sample_requests.list_sample_product_options")
    @patch("screen_sample_requests.get_bitable_access_token")
    @patch("lib.app_config.load_bitable_settings")
    def test_confirm_pass1_backfills_order_after_create(
        self,
        load_settings,
        get_token,
        list_options,
        confirm_approved,
        resolve_product,
        resolve_duplicate,
        create_record,
        update_order,
    ) -> None:
        load_settings.return_value = {"app_token": "app-test", "table_id": "tbl-test"}
        get_token.return_value = "token-test"
        list_options.return_value = ["328"]
        confirm_approved.return_value = {
            "ok": True,
            "state": "approved",
            "attempt": 1,
            "ready_to_ship": {
                "state": "pending-not-approvable",
                "main_order_id": "577532709908747188",
            },
        }
        resolve_product.return_value = {"ok": True, "sku": "328", "option": "328"}
        resolve_duplicate.return_value = {
            "status": "no-match",
            "record": None,
            "record_ids": [],
        }
        create_record.return_value = {
            "record_id": "rec-new",
            "fields": {},
            "raw": {},
        }
        update_order.return_value = {
            "status": "written",
            "record_id": "rec-new",
        }
        row = {
            "apply_id": "apply-a1",
            "creator_id": "creator-1",
            "creator_name": "alice",
            "product_id": "product-1",
            "approve_status": "approved",
            "approved_at": "2026-08-15T10:00:00",
        }

        _run_confirm_pipeline(
            store_id="store-test",
            candidates=[row],
            hero_data={"rows": []},
            write_feishu=True,
            execute_limit=1,
            config_path=None,
            order_backfill_rows=[],
        )

        create_record.assert_called_once()
        update_order.assert_called_once()
        self.assertEqual(update_order.call_args.args[2], "577532709908747188")
        self.assertEqual(update_order.call_args.args[1], "rec-new")
        self.assertEqual(row["approve_confirmation"], "confirmed")
        self.assertEqual(row["feishu_order_status"], "written")

    @patch("screen_sample_requests.update_record_order_no")
    @patch("screen_sample_requests.create_creator_relation_record")
    @patch("screen_sample_requests.resolve_duplicate_record")
    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.confirm_application_approved_api")
    @patch("screen_sample_requests.list_sample_product_options")
    @patch("screen_sample_requests.get_bitable_access_token")
    @patch("lib.app_config.load_bitable_settings")
    def test_confirm_ambiguous_feishu_match_never_creates_or_backfills(
        self,
        load_settings,
        get_token,
        list_options,
        confirm_approved,
        resolve_product,
        resolve_duplicate,
        create_record,
        update_order,
    ) -> None:
        load_settings.return_value = {"app_token": "app-test", "table_id": "tbl-test"}
        get_token.return_value = "token-test"
        list_options.return_value = ["328"]
        confirm_approved.return_value = {
            "ok": True,
            "state": "approved",
            "ready_to_ship": {
                "curr_status": 20,
                "main_order_id": "577532709908747188",
            },
        }
        resolve_product.return_value = {"ok": True, "sku": "328", "option": "328"}
        resolve_duplicate.return_value = {
            "status": "ambiguous-match",
            "record": None,
            "record_ids": ["rec-one", "rec-two"],
        }
        row = {
            "apply_id": "apply-ambiguous",
            "creator_id": "creator-1",
            "creator_name": "alice",
            "product_id": "product-1",
            "approve_status": "approved",
            "approved_at": "2026-08-15T10:00:00",
        }

        _run_confirm_pipeline(
            store_id="store-test",
            candidates=[row],
            hero_data={"rows": []},
            write_feishu=True,
            execute_limit=1,
            config_path=None,
        )

        create_record.assert_not_called()
        update_order.assert_not_called()
        self.assertEqual(row["approve_confirmation"], "confirmed")
        self.assertEqual(row["feishu_relation_status"], "ambiguous-match")
        self.assertEqual(row["feishu_duplicate_record_ids"], ["rec-one", "rec-two"])

    @patch("screen_sample_requests.update_record_order_no")
    @patch("screen_sample_requests.create_creator_relation_record")
    @patch("screen_sample_requests.resolve_duplicate_record")
    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.confirm_application_approved_api")
    @patch("screen_sample_requests.list_sample_product_options")
    @patch("screen_sample_requests.get_bitable_access_token")
    @patch("lib.app_config.load_bitable_settings")
    def test_confirm_missing_created_record_id_is_not_retried_automatically(
        self,
        load_settings,
        get_token,
        list_options,
        confirm_approved,
        resolve_product,
        resolve_duplicate,
        create_record,
        update_order,
    ) -> None:
        load_settings.return_value = {"app_token": "app-test", "table_id": "tbl-test"}
        get_token.return_value = "token-test"
        list_options.return_value = ["328"]
        confirm_approved.return_value = {
            "ok": True,
            "state": "approved",
            "ready_to_ship": {
                "curr_status": 20,
                "main_order_id": "577532709908747188",
            },
        }
        resolve_product.return_value = {"ok": True, "sku": "328", "option": "328"}
        resolve_duplicate.return_value = {
            "status": "no-match",
            "record": None,
            "record_ids": [],
        }
        create_record.return_value = {}
        row = {
            "apply_id": "apply-missing-record-id",
            "creator_id": "creator-1",
            "creator_name": "alice",
            "product_id": "product-1",
            "approve_status": "approved",
            "approved_at": "2026-08-15T10:00:00",
        }

        _run_confirm_pipeline(
            store_id="store-test",
            candidates=[row],
            hero_data={"rows": []},
            write_feishu=True,
            execute_limit=1,
            config_path=None,
        )

        create_record.assert_called_once()
        update_order.assert_not_called()
        self.assertEqual(row["approve_confirmation"], "confirmed")
        self.assertEqual(row["feishu_relation_status"], "write-uncertain")
        self.assertNotIn("feishu_record_id", row)

    @patch("screen_sample_requests.create_creator_relation_record")
    @patch("screen_sample_requests.resolve_duplicate_record")
    @patch("screen_sample_requests.confirm_application_approved_api")
    @patch("screen_sample_requests.list_sample_product_options")
    @patch("screen_sample_requests.get_bitable_access_token")
    @patch("lib.app_config.load_bitable_settings")
    def test_legacy_created_status_without_record_id_is_not_recreated(
        self,
        load_settings,
        get_token,
        list_options,
        confirm_approved,
        resolve_duplicate,
        create_record,
    ) -> None:
        load_settings.return_value = {"app_token": "app-test", "table_id": "tbl-test"}
        get_token.return_value = "token-test"
        list_options.return_value = ["328"]
        confirm_approved.return_value = {
            "ok": True,
            "state": "approved",
            "ready_to_ship": {
                "curr_status": 20,
                "main_order_id": "577532709908747188",
            },
        }
        row = {
            "apply_id": "apply-legacy-created",
            "creator_id": "creator-1",
            "creator_name": "alice",
            "product_id": "product-1",
            "approve_status": "approved",
            "feishu_status": "created",
        }

        _run_confirm_pipeline(
            store_id="store-test",
            candidates=[row],
            hero_data={"rows": []},
            write_feishu=True,
            execute_limit=1,
            config_path=None,
        )

        resolve_duplicate.assert_not_called()
        create_record.assert_not_called()
        self.assertEqual(row["feishu_relation_status"], "write-uncertain")

    @patch("screen_sample_requests.update_record_order_no")
    @patch("screen_sample_requests.check_application_in_tab_api")
    @patch("screen_sample_requests.create_creator_relation_record")
    @patch("screen_sample_requests.resolve_duplicate_record")
    @patch("screen_sample_requests.resolve_sample_product_for_row")
    @patch("screen_sample_requests.list_sample_product_options")
    @patch("screen_sample_requests.get_bitable_access_token")
    @patch("lib.app_config.load_bitable_settings")
    def test_confirmed_read_only_row_recovers_feishu_and_order_backfill(
        self,
        load_settings,
        get_token,
        list_options,
        resolve_product,
        resolve_duplicate,
        create_record,
        check_tab,
        update_order,
    ) -> None:
        load_settings.return_value = {"app_token": "app-test", "table_id": "tbl-test"}
        get_token.return_value = "token-test"
        list_options.return_value = ["328"]
        resolve_product.return_value = {"ok": True, "sku": "328", "option": "328"}
        resolve_duplicate.return_value = {
            "status": "no-match",
            "record": None,
            "record_ids": [],
        }
        create_record.return_value = {"record_id": "rec-recovered"}
        check_tab.return_value = {
            "ok": True,
            "state": "pending-not-approvable",
            "curr_status": 20,
            "main_order_id": "577532709908747188",
        }
        update_order.return_value = {"status": "written", "record_id": "rec-recovered"}
        row = {
            "apply_id": "apply-read-only",
            "creator_id": "creator-1",
            "creator_name": "alice",
            "product_id": "product-1",
            "approve_status": "approved",
            "approve_confirmation": "confirmed",
        }

        _run_confirm_pipeline(
            store_id="store-test",
            candidates=[],
            hero_data={"rows": []},
            write_feishu=True,
            execute_limit=1,
            config_path=None,
            reconciliation_rows=[row],
        )

        create_record.assert_called_once()
        check_tab.assert_called_once()
        update_order.assert_called_once()
        self.assertEqual(row["platform_confirmation_status"], "confirmed")
        self.assertEqual(row["feishu_relation_status"], "created")
        self.assertEqual(row["feishu_order_status"], "written")

    @patch("screen_sample_requests.update_record_order_no")
    @patch("screen_sample_requests.confirm_application_approved_api")
    @patch("screen_sample_requests.list_sample_product_options")
    @patch("screen_sample_requests.get_bitable_access_token")
    @patch("lib.app_config.load_bitable_settings")
    def test_existing_feishu_record_still_receives_platform_confirmation(
        self,
        load_settings,
        get_token,
        list_options,
        confirm_approved,
        update_order,
    ) -> None:
        load_settings.return_value = {"app_token": "app-test", "table_id": "tbl-test"}
        get_token.return_value = "token-test"
        list_options.return_value = ["328"]
        confirm_approved.return_value = {
            "ok": True,
            "state": "approved",
            "ready_to_ship": {
                "curr_status": 20,
                "main_order_id": "577532709908747188",
            },
        }
        update_order.return_value = {"status": "written", "record_id": "rec-existing"}
        row = {
            "apply_id": "apply-existing",
            "creator_id": "creator-1",
            "creator_name": "alice",
            "product_id": "product-1",
            "approve_status": "approved",
            "approved_at": "2026-08-15T10:00:00",
            "feishu_status": "created",
            "feishu_record_id": "rec-existing",
        }

        _run_confirm_pipeline(
            store_id="store-test",
            candidates=[row],
            hero_data={"rows": []},
            write_feishu=True,
            execute_limit=1,
            config_path=None,
        )

        confirm_approved.assert_called_once()
        update_order.assert_called_once()
        self.assertEqual(row["approve_confirmation"], "confirmed")
        self.assertEqual(row["feishu_record_id"], "rec-existing")

    def test_feishu_initialization_failure_can_recover_after_confirmation(self) -> None:
        row = {
            "apply_id": "apply-retry-feishu",
            "creator_id": "creator-1",
            "creator_name": "alice",
            "product_id": "product-1",
            "approve_status": "approved",
            "approved_at": "2026-08-15T10:00:00",
        }
        with (
            patch("lib.app_config.load_bitable_settings", return_value={}),
            patch(
                "screen_sample_requests.get_bitable_access_token",
                side_effect=RuntimeError("temporary Feishu outage"),
            ),
            patch(
                "screen_sample_requests.confirm_application_approved_api",
                return_value={
                    "ok": True,
                    "state": "approved",
                    "ready_to_ship": {
                        "curr_status": 20,
                        "main_order_id": "577532709908747188",
                    },
                },
            ),
        ):
            _run_confirm_pipeline(
                store_id="store-test",
                candidates=[row],
                hero_data={"rows": []},
                write_feishu=True,
                execute_limit=1,
                config_path=None,
            )

        self.assertEqual(row["approve_confirmation"], "confirmed")
        self.assertNotIn("feishu_record_id", row)

        with (
            patch(
                "lib.app_config.load_bitable_settings",
                return_value={"app_token": "app-test", "table_id": "tbl-test"},
            ),
            patch("screen_sample_requests.get_bitable_access_token", return_value="token-test"),
            patch("screen_sample_requests.list_sample_product_options", return_value=["328"]),
            patch(
                "screen_sample_requests.resolve_sample_product_for_row",
                return_value={"ok": True, "sku": "328", "option": "328"},
            ),
            patch(
                "screen_sample_requests.resolve_duplicate_record",
                return_value={
                    "status": "no-match",
                    "record": None,
                    "record_ids": [],
                },
            ),
            patch(
                "screen_sample_requests.create_creator_relation_record",
                return_value={"record_id": "rec-recovered"},
            ) as create_record,
            patch(
                "screen_sample_requests.check_application_in_tab_api",
                return_value={
                    "ok": True,
                    "state": "pending-not-approvable",
                    "curr_status": 20,
                    "main_order_id": "577532709908747188",
                },
            ),
            patch(
                "screen_sample_requests.update_record_order_no",
                return_value={"status": "written", "record_id": "rec-recovered"},
            ),
        ):
            _run_confirm_pipeline(
                store_id="store-test",
                candidates=[],
                hero_data={"rows": []},
                write_feishu=True,
                execute_limit=1,
                config_path=None,
                reconciliation_rows=[row],
            )

        create_record.assert_called_once()
        self.assertEqual(row["feishu_record_id"], "rec-recovered")
        self.assertEqual(row["feishu_order_status"], "written")

    @patch("screen_sample_requests.update_record_order_no")
    @patch("screen_sample_requests.check_application_in_tab_api")
    @patch("screen_sample_requests.list_sample_product_options")
    @patch("screen_sample_requests.get_bitable_access_token")
    @patch("lib.app_config.load_bitable_settings")
    def test_order_backfill_pass_respects_shared_limit(
        self,
        load_settings,
        get_token,
        list_options,
        check_tab,
        update_order,
    ) -> None:
        load_settings.return_value = {"app_token": "app-test", "table_id": "tbl-test"}
        get_token.return_value = "token-test"
        list_options.return_value = ["328"]
        check_tab.return_value = {
            "ok": True,
            "state": "pending-not-approvable",
            "curr_status": 20,
            "main_order_id": "577532709908747188",
        }
        update_order.return_value = {
            "status": "written",
            "record_id": "rec-1",
        }
        # 共享预算：先被 limit=1 的主流程用完，回填行不得再查再写
        candidate = {
            "apply_id": "apply-c1",
            "creator_id": "creator-1",
            "creator_name": "carol",
            "product_id": "product-1",
            "approve_status": "approved",
            "approved_at": "2026-08-15T10:00:00",
        }
        backfill_row = {
            "apply_id": "apply-b1",
            "creator_id": "creator-1",
            "creator_name": "bob",
            "product_id": "product-1",
            "approve_status": "approved",
            "approve_confirmation": "confirmed",
            "feishu_record_id": "rec-1",
            "approved_at": "2026-08-15T10:00:00",
        }
        with patch(
            "screen_sample_requests.confirm_application_approved_api",
            return_value={
                "ok": True,
                "state": "still-pending",
                "attempt": 1,
            },
        ):
            _run_confirm_pipeline(
                store_id="store-test",
                candidates=[candidate],
                hero_data={"rows": []},
                write_feishu=True,
                execute_limit=1,
                config_path=None,
                order_backfill_rows=[backfill_row],
            )

        check_tab.assert_not_called()
        update_order.assert_not_called()
        self.assertEqual(backfill_row["feishu_order_status"], "skipped-limit")


if __name__ == "__main__":
    unittest.main()
