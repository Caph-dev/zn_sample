"""Offline reconciliation contracts: real snapshots, mocked remote boundaries."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import auto_approval
from lib import auto_approval_reconciliation as reconciliation

PRODUCT_ID = "1732414717062320994"
ORDER_NO = "1234567890123456789"
RULE_PAYLOAD = {
    "schema_version": 1, "mode": "custom", "product_ids": [PRODUCT_ID],
    "basic": {"fulfillment": {"enabled": True, "min": 85}},
    "video_live": {"enabled": False}, "content": {"enabled": False},
}
HERO_DATA = {
    "hero_keys": {"B005", PRODUCT_ID}, "hero_product_ids": [PRODUCT_ID],
    "rows": [{"is_hero": True, "product_id": PRODUCT_ID, "sku": "B005"}],
}


def execution_row(apply_id="apply-1", **overrides):
    row = {
        "apply_id": apply_id, "creator_id": "creator-1", "creator_name": "creator_test",
        "product_id": PRODUCT_ID, "approve_status": "approved",
        "feishu_relation_status": "not-requested", "sku_desc": "3PCS,M",
        "sample_product_option": "B005",
    }
    row.update(overrides)
    return row


def inspection(apply_id="apply-1", **overrides):
    result = {
        "apply_id": apply_id, "creator_name": "creator_test", "product_id": PRODUCT_ID,
        "platform_status": "confirmed", "relation_status": "missing",
        "order_status": "missing", "status": "missing", "can_repair": True,
        "record_id": "", "order_no": ORDER_NO, "sample_product_option": "B005",
        "resolved_sku": "B005", "detail": "",
    }
    result.update(overrides)
    return result


class ReconciliationInspectionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        boundaries = {
            "load_bitable_settings": {"app_token": "test-app", "table_id": "test-table"},
            "get_bitable_access_token": "test-token",
            "list_sample_product_options": ["B005"],
            "confirm_application_approved_api": {
                "ok": True, "state": "approved",
                "ready_to_ship": {"curr_status": 20, "main_order_id": ORDER_NO},
            },
            "resolve_duplicate_record": {"status": "unique-match", "record": {"record_id": "record-1"}},
            "get_record_fields": {"红人ID": "creator_test", "寄样产品": "B005", "订单号": ORDER_NO},
        }
        for name, result in boundaries.items():
            self.mocks[name] = self.stack.enter_context(patch.object(reconciliation, name, return_value=result))
        self.create_record = self.stack.enter_context(patch("lib.feishu_bitable.create_creator_relation_record"))
        self.approve = self.stack.enter_context(patch("lib.sample_write_api.approve_application_api"))
        self.addCleanup(self.create_record.assert_not_called)
        self.addCleanup(self.approve.assert_not_called)

    def inspect(self, **row_overrides):
        return reconciliation.inspect_reconciliation_rows(
            [execution_row(**row_overrides)], store_id="store-1", hero_data=HERO_DATA, config_path=None,
        )[0]

    def test_unique_match_requires_actual_fields_readback(self):
        result = self.inspect(feishu_record_id="record-1", feishu_relation_status="created")
        self.assertEqual(result["status"], "verified")
        self.assertFalse(result["can_repair"])
        self.mocks["get_record_fields"].assert_called_once_with(
            "test-token", "record-1", app_token="test-app", table_id="test-table",
        )
        self.mocks["confirm_application_approved_api"].assert_called_once_with(
            "store-1", "apply-1", expected_creator_id="creator-1", expected_product_id=PRODUCT_ID,
            max_attempts=1,
        )

    def test_blank_order_is_repairable_but_different_order_is_not(self):
        for current_order, expected_status, repairable in (("", "missing", True), ("9999999999999999999", "conflict", False)):
            with self.subTest(order=current_order):
                self.mocks["get_record_fields"].return_value["订单号"] = current_order
                result = self.inspect()
                self.assertEqual(result["order_status"], expected_status)
                self.assertIs(result["can_repair"], repairable)

    def test_unique_record_wrong_key_or_record_id_never_repairs(self):
        for field, value in (("红人ID", "someone_else"), ("寄样产品", "B006")):
            with self.subTest(field=field):
                self.mocks["get_record_fields"].return_value = {"红人ID": "creator_test", "寄样产品": "B005", "订单号": "", field: value}
                result = self.inspect()
                self.assertEqual(result["relation_status"], "conflict")
                self.assertFalse(result["can_repair"])
        result = self.inspect(feishu_record_id="different-record")
        self.assertEqual(result["relation_status"], "conflict")
        self.assertFalse(result["can_repair"])

    def test_no_match_only_repairs_without_previous_write_evidence(self):
        self.mocks["resolve_duplicate_record"].return_value = {"status": "no-match"}
        self.assertTrue(self.inspect()["can_repair"])
        for previous in ("created", "linked-existing", "write-uncertain", "ambiguous-match", "invalid-match"):
            with self.subTest(previous=previous):
                result = self.inspect(feishu_relation_status=previous)
                self.assertFalse(result["can_repair"])
                self.assertNotEqual(result["relation_status"], "missing")
        self.assertFalse(self.inspect(feishu_record_id="record-1")["can_repair"])

    def test_ambiguous_and_failed_queries_are_never_missing(self):
        for status in ("ambiguous-match", "invalid-match", "permission-denied", "error", ""):
            with self.subTest(status=status):
                self.mocks["resolve_duplicate_record"].return_value = {"status": status}
                result = self.inspect()
                self.assertFalse(result["can_repair"])
                self.assertNotEqual(result["relation_status"], "missing")
        for boundary in ("get_bitable_access_token", "resolve_duplicate_record", "get_record_fields"):
            with self.subTest(boundary=boundary):
                self.mocks["resolve_duplicate_record"].return_value = {"status": "unique-match", "record": {"record_id": "record-1"}}
                self.mocks[boundary].side_effect = RuntimeError("permission denied")
                result = self.inspect()
                self.assertEqual(result["relation_status"], "error")
                self.assertFalse(result["can_repair"])
                self.mocks[boundary].side_effect = None

    def test_only_approved_or_unknown_execution_rows_are_inspected(self):
        for status in ("skipped", "failed", "", "pending"):
            with self.subTest(status=status):
                result = self.inspect(approve_status=status)
                self.assertFalse(result["can_repair"])
        self.mocks["confirm_application_approved_api"].assert_not_called()
        for status in ("approved", "unknown"):
            with self.subTest(status=status):
                self.assertEqual(self.inspect(approve_status=status)["status"], "verified")

    def test_platform_unavailable_or_identity_mismatch_blocks_repair(self):
        self.mocks["resolve_duplicate_record"].return_value = {"status": "no-match"}
        for platform in (
            {"ok": False, "state": "unknown"},
            {"ok": True, "state": "approved", "ready_to_ship": {"curr_status": 20, "state": "identity-mismatch", "main_order_id": ORDER_NO}},
            {"ok": True, "state": "approved", "ready_to_ship": {"curr_status": 30, "main_order_id": ORDER_NO}},
        ):
            with self.subTest(platform=platform):
                self.mocks["confirm_application_approved_api"].return_value = platform
                self.assertFalse(self.inspect()["can_repair"])
        self.mocks["confirm_application_approved_api"].side_effect = RuntimeError("offline")
        self.assertFalse(self.inspect()["can_repair"])

    def test_uncertain_order_write_and_changed_product_mapping_block_repair(self):
        self.mocks["get_record_fields"].return_value["订单号"] = ""
        for field in ("order_backfill_status", "feishu_order_status"):
            with self.subTest(field=field):
                self.assertFalse(self.inspect(**{field: "write-uncertain"})["can_repair"])
        self.assertFalse(self.inspect(sample_product_option="B006")["can_repair"])


class ReconciliationScriptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.rule = auto_approval.validate_custom_rule(RULE_PAYLOAD, allowed_product_ids={PRODUCT_ID})
        self.rows = [execution_row()]
        self.preview = {
            "envelope_type": "auto_approval_preview", "schema_version": 1, "mode": "custom",
            "store_id": "store-1", "rule": self.rule.to_dict(), "rule_hash": auto_approval.rule_hash(self.rule),
            "integrity_complete": True, "finished_at": datetime.now(timezone.utc).isoformat(),
            "allowed_product_ids": [PRODUCT_ID], "rows": self.rows,
        }
        self.snapshot = {
            "envelope_type": "auto_approval_reconcile_input", "execution_id": "execution-1",
            "store_id": "store-1", "rule_hash": auto_approval.rule_hash(self.rule),
            "rows": self.rows, "repair_apply_ids": ["apply-1"], "limit": 1,
        }
        self.args = Namespace(
            write_feishu=0, yes=False, store_id="store-1", rules=self.root / "rules.json",
            preview=self.root / "preview.json", backup=self.root / "snapshot.json",
            execution_id="execution-1", from_seller_home=False, config=None, limit=1,
            out=self.root / "result.json", apply_ids=self.root / "apply_ids.json",
        )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(auto_approval, "_load_hero", return_value=HERO_DATA))
        self.inspect_rows = self.stack.enter_context(patch.object(reconciliation, "inspect_reconciliation_rows", return_value=[inspection()]))
        self.confirm = self.stack.enter_context(patch.object(auto_approval, "_run_confirm_pipeline"))
        self.approve = self.stack.enter_context(patch.object(auto_approval, "approve_application_api"))
        self.create_record = self.stack.enter_context(patch.object(auto_approval, "create_creator_relation_record"))
        self.stack.enter_context(patch.object(auto_approval, "navigate_from_seller_home_to_pending"))
        self.addCleanup(self.approve.assert_not_called)
        self.addCleanup(self.create_record.assert_not_called)

    def write_inputs(self):
        for path, payload in ((self.args.rules, RULE_PAYLOAD), (self.args.preview, self.preview), (self.args.backup, self.snapshot), (self.args.apply_ids, ["apply-1"])):
            path.write_text(json.dumps(payload), encoding="utf-8")

    def run_reconcile(self):
        self.write_inputs()
        return auto_approval._run_reconcile(self.args)

    def test_read_only_needs_no_yes_and_never_calls_write_pipeline(self):
        self.assertEqual(self.run_reconcile(), 0)
        self.confirm.assert_not_called()
        self.inspect_rows.assert_called_once()
        report = json.loads(self.args.out.read_text(encoding="utf-8"))
        self.assertFalse(report["write_feishu"])
        self.assertEqual(report["execution_id"], "execution-1")
        self.assertEqual(report["rows"], self.rows)

    def test_repair_requires_explicit_yes_before_any_inspection(self):
        self.args.write_feishu = 1
        self.assertEqual(self.run_reconcile(), 2)
        self.confirm.assert_not_called()
        self.inspect_rows.assert_not_called()

    def test_repair_reads_before_and_after_and_checkpoints_uncertainty(self):
        self.args.write_feishu, self.args.yes = 1, True
        events = []

        def inspect_rows(rows, **kwargs):
            events.append("inspect")
            if len(events) == 1:
                return [inspection()]
            self.assertEqual(rows[0]["feishu_record_id"], "created-record")
            return [inspection(status="verified", can_repair=False)]

        def confirm(**kwargs):
            events.append("confirm")
            checkpoint = json.loads(self.args.out.read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["rows"][0]["feishu_relation_status"], "write-uncertain")
            self.assertFalse(checkpoint["items"][0]["can_repair"])
            self.assertEqual(kwargs["candidates"], kwargs["reconciliation_rows"])
            self.assertEqual(kwargs["execute_limit"], 1)
            row = kwargs["reconciliation_rows"][0]
            self.assertEqual(row["creator_id"], "creator-1")
            self.assertEqual(row["sample_product_option"], "B005")
            self.assertEqual(row["resolved_sku"], "B005")
            self.assertEqual(row["platform_confirmation_status"], "confirmed")
            row.update(feishu_record_id="created-record", feishu_relation_status="created")

        self.inspect_rows.side_effect = inspect_rows
        self.confirm.side_effect = confirm
        self.assertEqual(self.run_reconcile(), 0)
        self.assertEqual(events, ["inspect", "confirm", "inspect"])
        self.assertEqual(json.loads(self.args.out.read_text())["items"][0]["status"], "verified")

    def test_repair_selects_only_snapshot_authorized_fresh_missing_rows_with_limit(self):
        self.args.write_feishu, self.args.yes = 1, True
        self.snapshot["rows"] = [execution_row(f"apply-{index}") for index in range(1, 5)]
        self.preview["rows"] = self.snapshot["rows"]
        self.snapshot["repair_apply_ids"] = ["apply-2", "apply-3", "apply-4"]
        self.inspect_rows.return_value = [inspection("apply-1"), inspection("apply-2", can_repair=False), inspection("apply-3"), inspection("apply-4")]
        self.assertEqual(self.run_reconcile(), 0)
        self.assertEqual([row["apply_id"] for row in self.confirm.call_args.kwargs["reconciliation_rows"]], ["apply-3"])

    def test_no_snapshot_authorization_never_passes_rows_to_confirm(self):
        self.args.write_feishu, self.args.yes = 1, True
        self.snapshot["repair_apply_ids"] = []
        self.assertEqual(self.run_reconcile(), 2)
        self.confirm.assert_not_called()
        self.inspect_rows.assert_not_called()

    def test_snapshot_limit_cannot_be_increased_or_disabled_by_cli(self):
        self.args.write_feishu, self.args.yes = 1, True
        for limit in (0, -1, 2):
            with self.subTest(limit=limit):
                self.args.limit = limit
                self.assertEqual(self.run_reconcile(), 2)
        self.confirm.assert_not_called()
        self.inspect_rows.assert_not_called()

    def test_snapshot_row_identity_and_repair_ids_must_belong_to_preview(self):
        self.args.write_feishu, self.args.yes = 1, True
        for field in ("creator_id", "creator_name", "product_id", "apply_id"):
            with self.subTest(field=field):
                self.snapshot["rows"] = [{**self.rows[0], field: "foreign-value"}]
                self.assertEqual(self.run_reconcile(), 2)
        self.snapshot["rows"] = self.rows * 2
        self.assertEqual(self.run_reconcile(), 2)
        self.snapshot["rows"] = self.rows
        for repair_ids in (["foreign-apply"], [None], "apply-1", None):
            with self.subTest(repair_ids=repair_ids):
                self.snapshot["repair_apply_ids"] = repair_ids
                self.assertEqual(self.run_reconcile(), 2)
        self.confirm.assert_not_called()
        self.inspect_rows.assert_not_called()

    def test_expired_preview_allowed_only_for_reconciliation_not_execute(self):
        self.preview["finished_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.assertEqual(self.run_reconcile(), 0)
        self.args.yes = True
        self.assertEqual(auto_approval._run_execute(self.args), 2)
        self.confirm.assert_not_called()

    def test_cross_batch_store_hash_and_invalid_rows_are_rejected(self):
        original = dict(self.snapshot)
        for field, value in (("execution_id", "other-batch"), ("store_id", "other-store"), ("rule_hash", "wrong"), ("rows", []), ("rows", [{"creator_name": "missing-id"}])):
            with self.subTest(field=field, value=value):
                self.snapshot = {**original, field: value}
                self.assertEqual(self.run_reconcile(), 2)
        self.inspect_rows.assert_not_called()
        self.confirm.assert_not_called()

    def test_crash_during_repair_preserves_uncertain_intent(self):
        self.args.write_feishu, self.args.yes = 1, True
        self.confirm.side_effect = RuntimeError("worker interrupted")
        with self.assertRaisesRegex(RuntimeError, "worker interrupted"):
            self.run_reconcile()
        report = json.loads(self.args.out.read_text())
        self.assertEqual(report["rows"][0]["feishu_relation_status"], "write-uncertain")
        self.assertEqual(report["rows"][0]["order_backfill_status"], "write-uncertain")
        self.assertFalse(report["items"][0]["can_repair"])


if __name__ == "__main__":
    unittest.main()
