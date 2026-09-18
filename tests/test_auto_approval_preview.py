"""自动审批 preview：详情接口系统级故障时快速失败，不再逐行回退 DOM。

不触发任何网络/写操作；详情读取与扫表全部用内存桩替代。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import auto_approval  # noqa: E402
from lib.sample_data_source import (  # noqa: E402
    SYSTEMIC_DETAIL_FAILURE_LIMIT,
    CreatorDetailResult,
    is_systemic_detail_error,
)

HERO_PRODUCT_ID = "1732414717062320994"
SYSTEMIC_API_ERROR = (
    "/api/v1/oec/affiliate/creator/marketplace/profile "
    "业务失败 code=100000 message="
)

RULE_PAYLOAD = {
    "schema_version": 1,
    "mode": "custom",
    "product_ids": [HERO_PRODUCT_ID],
    "basic": {},
    "video_live": {
        "enabled": True,
        "logic": "either",
        "video": {"enabled": True, "gpm": 10, "avg_views": 300, "engagement": 2},
        "live": {"enabled": False},
    },
    "content": {"enabled": False},
}
HERO_DATA = {
    "hero_keys": {"B005", HERO_PRODUCT_ID},
    "hero_product_ids": [HERO_PRODUCT_ID],
    "rows": [{"is_hero": True, "product_id": HERO_PRODUCT_ID, "sku": "B005"}],
}


def _pending_rows(count: int) -> list[dict]:
    return [
        {
            "apply_id": f"apply-{index}",
            "creator_id": f"creator-{index}",
            "creator_name": f"creator_{index}",
            "product_id": HERO_PRODUCT_ID,
            "sku_desc": "3PCS (Best Seller),M",
            "can_be_approved": True,
            "follower_num": "5000",
            "gmv": "$2,000",
            "item_sold": "100",
            "content_video_views": "100000",
            "fulfillment_rate": "90%",
            "top_follower_gender": "Female 70%",
            "categories": "Womenswear & Underwear",
        }
        for index in range(count)
    ]


class SystemicDetailErrorTests(unittest.TestCase):
    def test_recognizes_known_systemic_failures(self) -> None:
        self.assertTrue(is_systemic_detail_error(SYSTEMIC_API_ERROR))
        self.assertTrue(
            is_systemic_detail_error("Please remove the plugin and try again")
        )

    def test_ignores_row_level_failures(self) -> None:
        self.assertFalse(is_systemic_detail_error(""))
        self.assertFalse(is_systemic_detail_error(None))
        self.assertFalse(
            is_systemic_detail_error("详情页资料读取失败：数据加载失败，请稍后刷新")
        )


class ExecuteSkuRecheckTests(unittest.TestCase):
    def test_live_six_piece_or_missing_sku_never_reaches_approval(self) -> None:
        rules_payload = {
            **RULE_PAYLOAD,
            "basic": {"fulfillment": {"enabled": True, "min": 85}},
            "video_live": {"enabled": False},
        }
        rule = auto_approval.validate_custom_rule(rules_payload, allowed_product_ids={HERO_PRODUCT_ID})
        row = auto_approval.enrich_row(_pending_rows(1)[0])
        evaluation = auto_approval.evaluate_custom_row(
            row, rule, hero_keys={HERO_PRODUCT_ID}, allowed_product_ids={HERO_PRODUCT_ID},
        )
        row.update(custom_eligible=True, custom_overall="passed", custom_checks=evaluation["checks"])
        preview = {
            "schema_version": 1,
            "mode": "custom",
            "store_id": "store-test",
            "rule": rule.to_dict(),
            "rule_hash": auto_approval.rule_hash(rule),
            "integrity_complete": True,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "allowed_product_ids": [HERO_PRODUCT_ID],
            "rows": [row],
        }
        for live_description in ("6PCS,L", None, ""):
            with self.subTest(sku=live_description), tempfile.TemporaryDirectory() as directory:
                rules_path = Path(directory) / "rules.json"
                preview_path = Path(directory) / "preview.json"
                apply_ids_path = Path(directory) / "apply_ids.json"
                for path, payload in (
                    (rules_path, rules_payload),
                    (preview_path, preview),
                    (apply_ids_path, [row["apply_id"]]),
                ):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                args = Namespace(
                    yes=True, limit=1, store_id="store-test", rules=rules_path, preview=preview_path,
                    apply_ids=apply_ids_path, from_seller_home=False, config=None, write_feishu=False,
                    backup_out=Path(directory) / "backup", out=Path(directory) / "result.json",
                    execution_id="execution-test", preview_id="preview-test",
                )
                with (
                    patch.object(auto_approval, "_load_hero", return_value=HERO_DATA),
                    patch.object(auto_approval, "check_pending_application_api", return_value={
                        "ok": True, "state": "pending-approvable", "sku_desc": live_description,
                    }),
                    patch.object(auto_approval, "approve_application_api") as approve,
                    patch.object(auto_approval, "create_creator_relation_record") as write_feishu,
                    patch.object(auto_approval, "_write_backup", return_value={}),
                ):
                    self.assertEqual(auto_approval._run_execute(args), 0)
                approve.assert_not_called()
                write_feishu.assert_not_called()
                result = json.loads(args.out.read_text(encoding="utf-8"))
                self.assertEqual(result["items"][0]["action"], "skipped-b005-sku")


class ExecuteCheckpointTests(unittest.TestCase):
    def test_accepted_approval_persists_platform_and_feishu_checkpoints(self) -> None:
        rules_payload = {
            **RULE_PAYLOAD,
            "basic": {"fulfillment": {"enabled": True, "min": 85}},
            "video_live": {"enabled": False},
        }
        rule = auto_approval.validate_custom_rule(
            rules_payload, allowed_product_ids={HERO_PRODUCT_ID},
        )
        row = auto_approval.enrich_row(_pending_rows(1)[0])
        evaluation = auto_approval.evaluate_custom_row(
            row, rule, hero_keys={HERO_PRODUCT_ID},
            allowed_product_ids={HERO_PRODUCT_ID},
        )
        row.update(
            custom_eligible=True, custom_overall="passed",
            custom_checks=evaluation["checks"],
        )
        preview = {
            "schema_version": 1,
            "mode": "custom",
            "store_id": "store-test",
            "rule": rule.to_dict(),
            "rule_hash": auto_approval.rule_hash(rule),
            "integrity_complete": True,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "allowed_product_ids": [HERO_PRODUCT_ID],
            "rows": [row],
        }
        scenarios = (
            (False, {"record_id": "record-test"}, None, "not-requested"),
            (True, {"record_id": "record-test"}, None, "created"),
            (True, {}, None, "write-uncertain"),
            (True, {}, auto_approval.FeishuBitableError("timeout"), "write-uncertain"),
        )
        for write_enabled, create_result, create_error, expected_status in scenarios:
            with self.subTest(write=write_enabled, status=expected_status, error=create_error):
                with tempfile.TemporaryDirectory() as directory:
                    temporary_root = Path(directory)
                    rules_path = temporary_root / "rules.json"
                    preview_path = temporary_root / "preview.json"
                    apply_ids_path = temporary_root / "apply_ids.json"
                    for file_path, payload in (
                        (rules_path, rules_payload),
                        (preview_path, preview),
                        (apply_ids_path, [row["apply_id"]]),
                    ):
                        file_path.write_text(json.dumps(payload), encoding="utf-8")
                    args = Namespace(
                        yes=True, limit=1, store_id="store-test",
                        rules=rules_path, preview=preview_path,
                        apply_ids=apply_ids_path, from_seller_home=False,
                        config=None, write_feishu=write_enabled,
                        backup_out=temporary_root / "backup",
                        out=temporary_root / "result.json",
                        execution_id="execution-test", preview_id="preview-test",
                    )
                    # Keep real validation, status setters and export persistence;
                    # replace every external read/write with an offline stub.
                    with (
                        patch.object(auto_approval, "_load_hero", return_value=HERO_DATA),
                        patch("lib.app_config.load_bitable_settings", return_value={}),
                        patch.object(auto_approval, "get_bitable_access_token", return_value="test-token"),
                        patch.object(auto_approval, "list_sample_product_options", return_value=["B005"]),
                        patch.object(auto_approval, "resolve_duplicate_record", return_value={"status": "no-match"}),
                        patch.object(auto_approval, "check_pending_application_api", return_value={
                            "ok": True, "state": "pending-approvable", "sku_desc": "3PCS,M",
                        }),
                        patch("lib.zclaw.ensure_store_exec_ready"),
                        patch.object(auto_approval, "approve_application_api", return_value={"ok": True}) as approve,
                        patch.object(
                            auto_approval, "create_creator_relation_record",
                            return_value=create_result, side_effect=create_error,
                        ) as create_relation,
                        patch.object(auto_approval.time, "sleep"),
                    ):
                        self.assertEqual(auto_approval._run_execute(args), 0)
                    approve.assert_called_once()
                    self.assertEqual(create_relation.call_count, int(write_enabled))
                    result = json.loads(args.out.read_text(encoding="utf-8"))
                    item = result["items"][0]
                    self.assertEqual(item["approve_status"], "approved")
                    self.assertEqual(item["platform_confirmation_status"], "deferred")
                    self.assertEqual(item["feishu_relation_status"], expected_status)
                    self.assertEqual(
                        item["feishu_record_id"],
                        "record-test" if expected_status == "created" else "",
                    )
                    backup = json.loads(
                        Path(result["backup_paths"]["json"]).read_text(encoding="utf-8")
                    )
                    self.assertEqual(backup[0]["approve_status"], "approved")
                    self.assertEqual(backup[0]["platform_confirmation_status"], "deferred")
                    self.assertEqual(backup[0]["feishu_relation_status"], expected_status)


class ApplyIdsFileTests(unittest.TestCase):
    def test_rejects_invalid_arrays_and_malformed_json(self) -> None:
        invalid_contents = [
            '{}', '"apply-1"', 'null', '[123]', '[null]', '[{}]',
            '[""]', '[" "]', '["apply-1", "apply-1"]', '[',
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apply_ids.json"
            for content in invalid_contents:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        auto_approval._load_apply_ids(path)

    def test_object_loader_still_rejects_arrays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text('["apply-1"]', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "JSON 不是对象"):
                auto_approval._load_json(path)


class PreviewFailFastTests(unittest.TestCase):
    def _run_with_detail_results(
        self,
        *,
        row_count: int,
        detail_result_factory,
    ) -> tuple[int, dict, list[str]]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            rules_path = root / "rules.json"
            rules_path.write_text(
                json.dumps(RULE_PAYLOAD, ensure_ascii=False),
                encoding="utf-8",
            )
            output_path = root / "preview.json"
            args = Namespace(
                store_id="store-test",
                rules=rules_path,
                from_seller_home=False,
                config=None,
                max_pages=1,
                max_rows=row_count,
                page_wait=0.0,
                preview_id="preview-test",
                detail_delay=0.0,
                out=output_path,
            )

            detail_calls: list[str] = []

            def fake_detail(store_id, row, *, data_source, list_href, wait):
                detail_calls.append(str(row.get("creator_name")))
                return detail_result_factory(row)

            with (
                patch.object(auto_approval, "_load_hero", return_value=HERO_DATA),
                patch.object(auto_approval, "load_creator_detail", side_effect=fake_detail),
                patch.object(
                    auto_approval,
                    "load_pending_rows",
                    return_value=SimpleNamespace(
                        rows=_pending_rows(row_count),
                        source_used="api",
                        fallback_reason="",
                    ),
                ),
            ):
                exit_code = auto_approval._run_preview(args)

            envelope = json.loads(output_path.read_text(encoding="utf-8"))
            return exit_code, envelope, detail_calls

    def test_stops_after_repeated_systemic_failures(self) -> None:
        def systemic_result(row):
            return CreatorDetailResult(
                result={
                    "ok": False,
                    "read_status": "failed",
                    "error_type": "profile-business-error",
                    "business_code": 100000,
                    "error": SYSTEMIC_API_ERROR,
                },
                source_used="dom-fallback",
                fallback_reason=SYSTEMIC_API_ERROR,
            )

        exit_code, envelope, detail_calls = self._run_with_detail_results(
            row_count=10,
            detail_result_factory=systemic_result,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(detail_calls), SYSTEMIC_DETAIL_FAILURE_LIMIT)
        self.assertFalse(envelope["integrity_complete"])
        self.assertTrue(
            any("系统级故障" in note for note in envelope["integrity_notes"]),
            envelope["integrity_notes"],
        )
        skipped = [
            row
            for row in envelope["rows"]
            if row.get("detail_error") == "detail-api-systemic-failure"
        ]
        self.assertEqual(len(skipped), 10 - SYSTEMIC_DETAIL_FAILURE_LIMIT)
        # 缺详情一律不通过（fail closed），不得出现误批。
        self.assertEqual(envelope["stats"]["eligible"], 0)
        for row in envelope["rows"]:
            self.assertNotEqual(row.get("custom_overall"), "passed")

    def test_row_level_failures_do_not_trip_the_breaker(self) -> None:
        def row_level_result(row):
            return CreatorDetailResult(
                result={
                    "ok": False,
                    "read_status": "failed",
                    "error_type": "detail-read-error",
                    "error": "详情页资料读取失败：not-on-detail",
                },
                source_used="dom-fallback",
                fallback_reason="PageApiError: 页面异步请求轮询超时",
            )

        exit_code, envelope, detail_calls = self._run_with_detail_results(
            row_count=5,
            detail_result_factory=row_level_result,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(detail_calls), 5)
        self.assertEqual(envelope["stats"]["eligible"], 0)

    def test_successful_rows_do_not_trip_the_breaker(self) -> None:
        def successful_result(row):
            return CreatorDetailResult(
                result={
                    "ok": True,
                    "read_status": "complete",
                    "detail": {
                        "video_gpm_n": 15.0,
                        "avg_video_views_n": 800.0,
                        "video_engagement_n": 3.0,
                    },
                },
                source_used="api",
            )

        exit_code, envelope, detail_calls = self._run_with_detail_results(
            row_count=4,
            detail_result_factory=successful_result,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(detail_calls), 4)
        self.assertEqual(
            [row.get("detail_error") for row in envelope["rows"]],
            [None, None, None, None],
        )


if __name__ == "__main__":
    unittest.main()
