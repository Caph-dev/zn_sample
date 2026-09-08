from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import screen_sample_requests as screen
from lib.export_util import CN_HEADERS, EXPORT_FIELDS, _write_xlsx_minimal, write_reports


class ContentReviewIntegrationTests(unittest.TestCase):
    @patch("screen_sample_requests.review_creator_rows")
    def test_sales_failures_and_list_only_never_run_paid_review(self, review):
        for formal_detail, eligible, detail_checked in (
            (True, False, True),
            (False, True, True),
            (False, True, False),
            (True, True, False),
        ):
            with self.subTest(formal=formal_detail, sales=eligible, detail=detail_checked):
                row = {"eligible": eligible, "detail_checked": detail_checked, "reason": "sales"}
                screen._review_after_sales([row], formal_detail=formal_detail)
                self.assertFalse(row["eligible"])
                self.assertEqual(row["sales_eligible"], eligible)
                self.assertEqual(row["sales_reason"], "sales")
                self.assertEqual(row["content_review_status"], "not_run")
        review.assert_not_called()

    @patch("screen_sample_requests.validate_content_review", return_value=(True, "valid mock proof"))
    @patch("screen_sample_requests.review_creator_rows")
    def test_review_receives_only_sales_and_required_detail_passes(self, review, validate):
        def review_passes(rows):
            return [dict(row, eligible=True, content_review_status="passed") for row in rows]

        review.side_effect = review_passes
        failed = {"creator_name": "failed", "eligible": False, "reason": "low GMV"}
        passed = {"creator_name": "passed", "eligible": True, "detail_checked": True, "reason": "sales passed"}
        screen._review_after_sales([failed, passed], formal_detail=True)
        self.assertEqual(len(review.call_args.args[0]), 1)
        self.assertEqual(review.call_args.args[0][0]["creator_name"], "passed")
        self.assertFalse(failed["eligible"])
        self.assertTrue(passed["eligible"])
        self.assertEqual(passed["screening_stage"], "终筛通过")
        validate.assert_called_once()

    @patch("screen_sample_requests.review_creator_rows", side_effect=RuntimeError("offline failure"))
    def test_review_exception_fails_closed(self, review):
        row = {"eligible": True, "detail_checked": True}
        screen._review_after_sales([row], formal_detail=True)
        self.assertFalse(row["eligible"])
        self.assertEqual(row["content_review_status"], "needs_review")

    @patch("screen_sample_requests.review_creator_rows")
    def test_review_identity_mismatch_fails_closed(self, review):
        row = {"creator_name": "alice", "eligible": True, "detail_checked": True}
        review.return_value = [{"creator_name": "bob", "eligible": True}]
        screen._review_after_sales([row], formal_detail=True)
        self.assertFalse(row["eligible"])
        self.assertEqual(row["creator_name"], "alice")

    def test_legacy_true_is_blocked_but_confirm_history_is_untouched(self):
        legacy = {"creator_name": "alice", "apply_id": "123", "eligible": True}
        historical = dict(legacy, approve_status="approved")
        before = dict(historical)
        self.assertEqual(screen._select_execute_candidates_from_export([legacy, historical]), [])
        self.assertFalse(legacy["eligible"])
        self.assertEqual(legacy["action"], "skipped-content-review")
        self.assertEqual(historical, before)
        self.assertEqual(screen._select_confirm_candidates_from_export([historical], force=True), [historical])

    @patch("screen_sample_requests.create_creator_relation_record")
    @patch("screen_sample_requests.approve_application_api")
    @patch("screen_sample_requests.click_approve_for_apply_id")
    @patch("screen_sample_requests.check_pending_application_api")
    def test_direct_pipeline_cannot_bypass_proof_guard(self, preflight, approve_dom, approve_api, create_record):
        row = {"creator_name": "alice", "apply_id": "123", "eligible": True, "sales_eligible": True}
        screen._run_execute_pipeline(
            store_id="offline", candidates=[row], hero_data={}, write_feishu=False,
            execute_limit=1, execute_delay=0, config_path=None, page_wait=0,
            observe_approve_network=False,
        )
        for operation in (preflight, approve_dom, approve_api, create_record):
            operation.assert_not_called()
        self.assertEqual(row["action"], "skipped-content-review")

    @patch("screen_sample_requests.validate_content_review", side_effect=RuntimeError("invalid evidence"))
    def test_validator_failure_is_not_an_approval_bypass(self, validate):
        row = {"creator_name": "alice", "apply_id": "123", "eligible": True, "sales_eligible": True}
        self.assertEqual(screen._select_execute_candidates_from_export([row]), [])
        self.assertTrue(row["approve_forbidden"])


class ContentReviewExportTests(unittest.TestCase):
    def test_new_fields_survive_json_csv_and_minimal_xlsx(self):
        row = {
            "creator_name": "alice", "sales_eligible": True, "sales_reason": "sales passed",
            "content_review_status": "passed", "content_review_reason": "verified",
            "content_review_handle": "alice", "content_review_window_start": "2026-08-30T00:00:00+00:00",
            "content_review_window_end": "2026-09-06T00:00:00+00:00",
            "content_review_related_count": 4, "content_review_complete": True,
            "content_review_evidence_path": "/offline/evidence.json", "content_review_version": "test-version",
            "content_review_model": "offline-model", "content_review_video_ids": ["111", "222", "333", "444"],
            "content_review_reviewed_at": "2026-09-06T00:00:00+00:00",
        }
        self.assertEqual(len(EXPORT_FIELDS), len(CN_HEADERS))
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "screen"
            paths = write_reports([row], prefix)
            loaded = screen._load_export_rows(paths["json"])[0]
            for field, value in row.items():
                self.assertEqual(loaded[field], value)
            with paths["csv"].open(encoding="utf-8-sig", newline="") as handle:
                records = list(csv.reader(handle))
            csv_values = dict(zip(EXPORT_FIELDS, records[1]))
            self.assertEqual(json.loads(csv_values["content_review_video_ids"]), row["content_review_video_ids"])
            self.assertEqual(csv_values["content_review_complete"], "Y")
            self.assertEqual(csv_values["content_review_evidence_path"], row["content_review_evidence_path"])
            spreadsheet = _write_xlsx_minimal([row], prefix.with_suffix(".xlsx"))
            with zipfile.ZipFile(spreadsheet) as archive:
                worksheet = archive.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("/offline/evidence.json", worksheet)
            self.assertIn("test-version", worksheet)


if __name__ == "__main__":
    unittest.main()
