"""自动审批服务层：执行边界、幂等与候选校验（无网络、无写操作）。"""
from __future__ import annotations

import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import (
    AutoApprovalCandidate,
    AutoApprovalExecution,
    AutoApprovalExecutionItem,
    AutoApprovalPreview,
    Base,
    Job,
)
from assistant.services.auto_approval_service import (
    AutoApprovalServiceError,
    create_execution,
    load_rule_draft,
    save_rule_draft,
)
from lib.auto_approval_sku import evaluate_product_sku

ALLOWED_PRODUCTS = [{"product_id": "1732414717062320994", "sku": "B005"}]


def _valid_rule_payload() -> dict:
    return {
        "schema_version": 1,
        "mode": "custom",
        "product_ids": ["1732414717062320994"],
        "basic": {"fulfillment": {"enabled": True, "min": 85}},
        "video_live": {"enabled": False},
        "content": {"enabled": False},
    }


def _mock_hero():
    return {
        "ok": True,
        "products": ALLOWED_PRODUCTS,
        "hero_keys": ["1732414717062320994"],
        "doc_title": "",
        "sheet_title": "",
    }


class ExecutionBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        engine = create_database_engine(
            Path(self.temporary_directory.name) / "assistant.sqlite3"
        )
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.hero_patcher = patch(
            "assistant.services.auto_approval_service.load_hero_products",
            return_value=_mock_hero(),
        )
        self.hero_patcher.start()
        with self.session_factory() as session:
            finished_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            preview = AutoApprovalPreview(
                id="preview-1",
                store_id="store-1",
                job_id="job-1",
                rule_json="{}",
                rule_hash="hash-1",
                rule_summary="[]",
                rules_path="",
                result_path="",
                status="completed",
                integrity_complete=True,
                stats="{}",
                created_at=datetime.now(timezone.utc),
                finished_at=finished_at,
            )
            session.add(preview)
            session.flush()
            session.add(
                AutoApprovalCandidate(
                    preview_id="preview-1",
                    apply_id="apply-1",
                    creator_id="creator-1",
                    creator_name="creator_test",
                    product_id="1732414717062320994",
                    overall="passed",
                    content_verdict="not_checked",
                    custom_eligible=True,
                    metrics_json=json.dumps({"sku_desc": "3PCS (Best Seller),XL"}),
                    checks_json=json.dumps([evaluate_product_sku({
                        "product_id": "1732414717062320994",
                        "sku_desc": "3PCS (Best Seller),XL",
                    })]),
                )
            )
            session.commit()

    def tearDown(self) -> None:
        self.hero_patcher.stop()
        self.temporary_directory.cleanup()

    def _create(self, **overrides) -> dict:
        payload = {
            "preview_id": "preview-1",
            "apply_ids": ["apply-1"],
            "limit": 1,
            "write_feishu": False,
            "confirmation": "y",
            "idempotency_key": "key-1",
        }
        payload.update(overrides)
        return create_execution(self.session_factory, **payload)

    def _complete_job(self, execution_id: str, approve_status: str) -> None:
        with self.session_factory() as session:
            execution = session.get(AutoApprovalExecution, execution_id)
            execution.status = "completed"
            session.get(Job, execution.job_id).status = "completed"
            session.query(AutoApprovalExecutionItem).filter_by(
                execution_id=execution_id
            ).one().approve_status = approve_status
            session.commit()

    def test_confirmation_required(self) -> None:
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(confirmation="n")
        self.assertEqual(context.exception.code, "confirmation-required")

    def test_legacy_b005_candidate_without_sku_evidence_cannot_execute(self) -> None:
        from sqlalchemy import select

        with self.session_factory() as session:
            candidate = session.scalar(select(AutoApprovalCandidate))
            candidate.checks_json = "[]"
            session.commit()
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create()
        self.assertEqual(context.exception.code, "b005-sku-evidence-invalid")

    def test_b005_six_piece_metrics_cannot_reuse_passed_evidence(self) -> None:
        from sqlalchemy import select

        with self.session_factory() as session:
            candidate = session.scalar(select(AutoApprovalCandidate))
            candidate.metrics_json = json.dumps({"sku_desc": "6PCS,L"})
            session.commit()
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create()
        self.assertEqual(context.exception.code, "b005-sku-evidence-invalid")

    def test_preview_sync_preserves_sku_and_check_for_page_and_execution(self) -> None:
        from assistant.services.auto_approval_service import preview_payload, sync_preview_result

        row = {
            "apply_id": "apply-1", "creator_id": "creator-1", "creator_name": "creator_test",
            "product_id": "1732414717062320994", "sku_desc": "3PCS (Best Seller),XL",
            "sku_id": "sku-test", "custom_overall": "passed", "custom_eligible": True,
            "content_verdict": "not_checked",
        }
        row["custom_checks"] = [evaluate_product_sku(row)]
        result_path = Path(self.temporary_directory.name) / "preview.json"
        result_path.write_text(json.dumps({"integrity_complete": True, "rows": [row]}), encoding="utf-8")
        with self.session_factory() as session:
            session.get(AutoApprovalPreview, "preview-1").result_path = str(result_path)
            session.commit()
        sync_preview_result(self.session_factory, "preview-1", status="completed")
        result = preview_payload(self.session_factory, "preview-1")["rows"][0]
        self.assertEqual(result["metrics"]["sku_desc"], row["sku_desc"])
        self.assertEqual(result["checks"], row["custom_checks"])
        self.assertIn("execution_id", self._create())

    def test_limit_zero_is_not_unlimited(self) -> None:
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(limit=0)
        self.assertEqual(context.exception.code, "invalid-limit")

    def test_limit_has_no_upper_cap(self) -> None:
        """限量只要求正整数，不再有 10 条上限。"""
        created = self._create(limit=99)
        self.assertIn("execution_id", created)

    def test_selection_over_limit_rejected(self) -> None:
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(limit=1, apply_ids=["apply-1", "apply-2"])
        self.assertEqual(context.exception.code, "selection-over-limit")

    def test_duplicate_apply_ids_rejected(self) -> None:
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(limit=2, apply_ids=["apply-1", "apply-1"])
        self.assertEqual(context.exception.code, "duplicate-apply-id")

    def test_tampered_candidate_rejected(self) -> None:
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(apply_ids=["apply-evil"])
        self.assertEqual(context.exception.code, "candidate-not-eligible")

    def test_unknown_preview_rejected(self) -> None:
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(preview_id="preview-missing")
        self.assertEqual(context.exception.code, "preview-not-found")

    def test_stale_preview_rejected(self) -> None:
        with self.session_factory() as session:
            preview = session.get(AutoApprovalPreview, "preview-1")
            preview.finished_at = datetime.now(timezone.utc) - timedelta(hours=25)
            session.commit()
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(idempotency_key="key-2")
        self.assertEqual(context.exception.code, "preview-stale")

    def test_incomplete_preview_rejected(self) -> None:
        with self.session_factory() as session:
            preview = session.get(AutoApprovalPreview, "preview-1")
            preview.integrity_complete = False
            session.commit()
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(idempotency_key="key-3")
        self.assertEqual(context.exception.code, "preview-incomplete")

    def test_idempotent_key_returns_same_execution(self) -> None:
        first = self._create()
        second = self._create()
        self.assertEqual(first["execution_id"], second["execution_id"])
        self.assertTrue(second["deduplicated"])

    def test_same_preview_can_execute_different_applications_separately(self) -> None:
        with self.session_factory() as session:
            session.add(AutoApprovalCandidate(
                preview_id="preview-1", apply_id="apply-2", creator_id="creator-2",
                creator_name="another_creator", product_id=ALLOWED_PRODUCTS[0]["product_id"],
                overall="passed", content_verdict="not_checked", custom_eligible=True,
                metrics_json=json.dumps({"sku_desc": "3PCS (Best Seller),XL"}),
                checks_json=json.dumps([evaluate_product_sku({
                    "product_id": ALLOWED_PRODUCTS[0]["product_id"],
                    "sku_desc": "3PCS (Best Seller),XL",
                })]),
            ))
            session.commit()
        first = self._create()
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(apply_ids=["apply-2"], idempotency_key="key-2")
        self.assertEqual(context.exception.code, "approval-already-running")
        self._complete_job(first["execution_id"], "approved")
        second = self._create(apply_ids=["apply-2"], idempotency_key="key-2")
        self.assertNotEqual(first["execution_id"], second["execution_id"])

    def test_existing_application_requires_reconciliation_or_other_selection(self) -> None:
        created = self._create()
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(idempotency_key="key-2")
        self.assertEqual(context.exception.code, "approval-already-running")

        for approve_status in ("approved", "unknown"):
            self._complete_job(created["execution_id"], approve_status)
            with self.assertRaises(AutoApprovalServiceError) as context:
                self._create(idempotency_key=f"key-{approve_status}")
            self.assertEqual(context.exception.code, "candidate-already-handled")

    def test_failed_application_can_be_reselected(self) -> None:
        created = self._create()
        self._complete_job(created["execution_id"], "failed")
        retried = self._create(idempotency_key="key-2")
        self.assertNotEqual(created["execution_id"], retried["execution_id"])

    def test_interrupted_operation_without_item_outcome_requires_review(self) -> None:
        created = self._create()
        with self.session_factory() as session:
            session.get(AutoApprovalExecution, created["execution_id"]).status = "failed"
            session.get(Job, created["job_id"]).status = "failed"
            session.commit()
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(idempotency_key="key-2")
        self.assertEqual(context.exception.code, "candidate-already-handled")

    def test_approved_application_cannot_be_reselected_from_new_preview(self) -> None:
        created = self._create()
        self._complete_job(created["execution_id"], "approved")
        with self.session_factory() as session:
            session.add(AutoApprovalPreview(
                id="preview-2", store_id="store-1", job_id="preview-job-2",
                rule_json="{}", rule_hash="hash-1", status="completed",
                integrity_complete=True, finished_at=datetime.now(timezone.utc),
            ))
            session.flush()
            previous_candidate = session.query(AutoApprovalCandidate).filter_by(
                preview_id="preview-1"
            ).one()
            session.add(AutoApprovalCandidate(
                preview_id="preview-2", apply_id=previous_candidate.apply_id,
                creator_id=previous_candidate.creator_id,
                creator_name=previous_candidate.creator_name,
                product_id=previous_candidate.product_id,
                custom_eligible=True, metrics_json=previous_candidate.metrics_json,
                checks_json=previous_candidate.checks_json,
            ))
            session.commit()
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(preview_id="preview-2", idempotency_key="key-2")
        self.assertEqual(context.exception.code, "candidate-already-handled")

    def test_successful_creation_persists_items(self) -> None:
        result = self._create()
        with self.session_factory() as session:
            execution = session.get(AutoApprovalExecution, result["execution_id"])
            self.assertIsNotNone(execution)
            self.assertEqual(execution.limit_count, 1)
            self.assertFalse(execution.write_feishu)
            items = (
                session.query(AutoApprovalExecutionItem)
                .filter_by(execution_id=result["execution_id"])
                .all()
            )
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].apply_id, "apply-1")


class RuleDraftMemoryTests(unittest.TestCase):
    """自定义规则记忆：严格校验后才保存，读取时再次校验。"""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        engine = create_database_engine(
            Path(self.temporary_directory.name) / "assistant.sqlite3"
        )
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.hero_patcher = patch(
            "assistant.services.auto_approval_service.load_hero_products",
            return_value=_mock_hero(),
        )
        self.hero_patcher.start()

    def tearDown(self) -> None:
        self.hero_patcher.stop()
        self.temporary_directory.cleanup()

    def test_no_saved_draft_returns_none(self) -> None:
        self.assertIsNone(load_rule_draft(self.session_factory))

    def test_save_and_load_round_trip(self) -> None:
        saved = save_rule_draft(self.session_factory, _valid_rule_payload())
        loaded = load_rule_draft(self.session_factory)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["rule"], saved["rule"])
        self.assertEqual(loaded["saved_at"], saved["saved_at"])
        self.assertEqual(loaded["rule"]["basic"]["fulfillment"]["min"], 85)

    def test_invalid_draft_rejected(self) -> None:
        payload = _valid_rule_payload()
        payload["basic"] = {}  # 全部关闭 → 空检查组合
        with self.assertRaises(AutoApprovalServiceError) as context:
            save_rule_draft(self.session_factory, payload)
        self.assertEqual(context.exception.code, "empty-check-set")

    def test_unknown_field_rejected(self) -> None:
        payload = _valid_rule_payload()
        payload["evil"] = True
        with self.assertRaises(AutoApprovalServiceError) as context:
            save_rule_draft(self.session_factory, payload)
        self.assertEqual(context.exception.code, "unknown-field")

    def test_load_ignores_product_no_longer_allowed(self) -> None:
        save_rule_draft(self.session_factory, _valid_rule_payload())
        self.hero_patcher.stop()
        self.hero_patcher = patch(
            "assistant.services.auto_approval_service.load_hero_products",
            return_value={"ok": True, "products": [], "hero_keys": []},
        )
        self.hero_patcher.start()
        self.assertIsNone(load_rule_draft(self.session_factory))

    def test_options_payload_includes_saved_rule(self) -> None:
        from assistant.services.auto_approval_service import options_payload
        from lib.auto_approval_rules import validate_custom_rule

        saved = save_rule_draft(self.session_factory, _valid_rule_payload())
        payload = options_payload(self.session_factory)
        self.assertIsNotNone(payload["saved_rule"])
        expected = validate_custom_rule(
            _valid_rule_payload(),
            allowed_product_ids={"1732414717062320994"},
        ).to_dict()
        self.assertEqual(payload["saved_rule"]["rule"], expected)
        self.assertEqual(payload["saved_rule"]["rule"], saved["rule"])

    def test_options_payload_without_session_has_no_saved_rule(self) -> None:
        from assistant.services.auto_approval_service import options_payload

        payload = options_payload()
        self.assertIsNone(payload["saved_rule"])


if __name__ == "__main__":
    unittest.main()
