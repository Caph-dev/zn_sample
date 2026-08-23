from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.export_util import (  # noqa: E402
    CN_HEADERS,
    EXPORT_FIELDS,
    RECONCILIATION_STAGE_APPROVE,
    RECONCILIATION_STAGE_CONFIRM,
    RECONCILIATION_STAGE_SCREEN,
    ReconciliationManifestError,
    latest_screen_export,
    load_reconciliation_manifest_for_export,
    reconciliation_manifest_path,
    resolve_reconciliation_export_arg,
    resolve_from_export_arg,
    prepare_export_row,
    validate_reconciliation_export_input,
    write_reconciliation_manifest,
    write_reports,
)


class LatestScreenExportTests(unittest.TestCase):
    def test_picks_newest_and_skips_pre_execute(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            older = folder / "sample_screen_20260815_090000.json"
            newer = folder / "sample_screen_20260815_100000.json"
            backup = folder / "sample_screen_20260815_100100_pre_execute.json"
            manifest = folder / "sample_screen_20260815_100200_reconcile.json"
            older.write_text("[]", encoding="utf-8")
            newer.write_text("[]", encoding="utf-8")
            backup.write_text("[]", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            self.assertEqual(latest_screen_export(folder), newer)
            self.assertEqual(resolve_from_export_arg(str(newer)), newer)

    def test_missing_export_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(FileNotFoundError):
                latest_screen_export(Path(raw))

    def test_blank_or_latest_uses_helper(self) -> None:
        self.assertIsNone(resolve_from_export_arg(None))

    def test_write_reports_skips_xlsx(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            prefix = Path(raw) / "sample_screen_x"
            paths = write_reports([{"creator_name": "a", "eligible": True}], prefix)
            self.assertIn("csv", paths)
            self.assertIn("json", paths)
            self.assertNotIn("xlsx", paths)
            self.assertTrue(paths["csv"].is_file())
            self.assertTrue(paths["json"].is_file())
            self.assertFalse(prefix.with_suffix(".xlsx").exists())

    def test_stage_aware_latest_uses_verified_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            screen_prefix = folder / "sample_screen_20260815_090000"
            approve_prefix = folder / "sample_screen_20260815_100000_approved"
            confirm_prefix = folder / "sample_screen_20260815_101000_confirm"
            screen_json = write_reports([], screen_prefix)["json"]
            write_reconciliation_manifest(
                [],
                out_prefix=screen_prefix,
                stage=RECONCILIATION_STAGE_SCREEN,
                store_id="store-test",
            )
            approve_json = write_reports([], approve_prefix)["json"]
            write_reconciliation_manifest(
                [],
                out_prefix=approve_prefix,
                stage=RECONCILIATION_STAGE_APPROVE,
                store_id="store-test",
                source_export=screen_json,
            )
            confirm_json = write_reports([], confirm_prefix)["json"]
            write_reconciliation_manifest(
                [],
                out_prefix=confirm_prefix,
                stage=RECONCILIATION_STAGE_CONFIRM,
                store_id="store-test",
                source_export=approve_json,
            )

            self.assertEqual(
                resolve_reconciliation_export_arg(
                    "latest",
                    allowed_stages={RECONCILIATION_STAGE_SCREEN},
                    exports_dir=folder,
                ).resolve(),
                screen_json.resolve(),
            )
            self.assertEqual(
                resolve_reconciliation_export_arg(
                    "latest",
                    allowed_stages={
                        RECONCILIATION_STAGE_APPROVE,
                        RECONCILIATION_STAGE_CONFIRM,
                    },
                    exports_dir=folder,
                ).resolve(),
                confirm_json.resolve(),
            )

    def test_manifest_rejects_modified_export_and_store_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            prefix = folder / "sample_screen_20260815_100000_approved"
            output_json = write_reports(
                [{"apply_id": "apply-1", "creator_id": "creator-1"}],
                prefix,
            )["json"]
            write_reconciliation_manifest(
                [{"apply_id": "apply-1", "creator_id": "creator-1"}],
                out_prefix=prefix,
                stage=RECONCILIATION_STAGE_APPROVE,
                store_id="store-a",
            )

            with self.assertRaisesRegex(ReconciliationManifestError, "店铺"):
                validate_reconciliation_export_input(
                    output_json,
                    allowed_stages={RECONCILIATION_STAGE_APPROVE},
                    store_id="store-b",
                )

            output_json.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ReconciliationManifestError, "已变更"):
                load_reconciliation_manifest_for_export(output_json)

    def test_latest_reconcile_rejects_newer_invalid_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            older_prefix = folder / "sample_screen_20260815_100000_approved"
            newer_prefix = folder / "sample_screen_20260815_101000_approved"
            older_json = write_reports([], older_prefix)["json"]
            write_reconciliation_manifest(
                [],
                out_prefix=older_prefix,
                stage=RECONCILIATION_STAGE_APPROVE,
                store_id="store-test",
            )
            newer_json = write_reports([], newer_prefix)["json"]
            write_reconciliation_manifest(
                [],
                out_prefix=newer_prefix,
                stage=RECONCILIATION_STAGE_APPROVE,
                store_id="store-test",
            )
            manifest_path = reconciliation_manifest_path(newer_json)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["created_at"] = "2030-01-01T00:00:00+00:00"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            newer_json.write_text("[]\n", encoding="utf-8")

            self.assertTrue(older_json.is_file())
            with self.assertRaisesRegex(ReconciliationManifestError, "已变更"):
                resolve_reconciliation_export_arg(
                    "latest",
                    allowed_stages={RECONCILIATION_STAGE_APPROVE},
                    exports_dir=folder,
                )

    def test_reconciliation_export_includes_statuses_and_safe_next_action(self) -> None:
        completed = prepare_export_row(
            {
                "apply_id": "apply-complete",
                "approve_confirmation": "confirmed",
                "feishu_status": "created",
                "feishu_record_id": "rec-complete",
                "feishu_order_status": "written",
            }
        )
        ambiguous = prepare_export_row(
            {
                "apply_id": "apply-ambiguous",
                "platform_confirmation_status": "confirmed",
                "feishu_relation_status": "ambiguous-match",
            }
        )

        self.assertEqual(len(EXPORT_FIELDS), len(CN_HEADERS))
        self.assertEqual(completed["platform_confirmation_status"], "confirmed")
        self.assertEqual(completed["order_backfill_status"], "written")
        self.assertIn("已完成", completed["reconcile_next_action"])
        self.assertIn("不要自动写入", ambiguous["reconcile_next_action"])
        self.assertEqual(prepare_export_row({})["reconcile_next_action"], "")


if __name__ == "__main__":
    unittest.main()
