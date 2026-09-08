"""自动审批服务层：执行边界、幂等与候选校验（无网络、无写操作）。"""
from __future__ import annotations

import sys
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
)
from assistant.services.auto_approval_service import (
    AutoApprovalServiceError,
    create_execution,
)

ALLOWED_PRODUCTS = [{"product_id": "1732414717062320994", "sku": "B005"}]


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

    def test_confirmation_required(self) -> None:
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(confirmation="n")
        self.assertEqual(context.exception.code, "confirmation-required")

    def test_limit_zero_is_not_unlimited(self) -> None:
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(limit=0)
        self.assertEqual(context.exception.code, "invalid-limit")

    def test_limit_over_cap_rejected(self) -> None:
        with self.assertRaises(AutoApprovalServiceError) as context:
            self._create(limit=99)
        self.assertEqual(context.exception.code, "limit-too-large")

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


if __name__ == "__main__":
    unittest.main()
