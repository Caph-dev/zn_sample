"""Web-job cancellation: the sentinel file and the tracking script checkpoint."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.job_cancel import (  # noqa: E402
    CANCEL_FLAG_ENV,
    EXIT_CODE_CANCELLED,
    cancellation_requested,
)


class CancellationRequestedTests(unittest.TestCase):
    def test_without_env_var_nothing_is_requested(self) -> None:
        self.assertFalse(cancellation_requested({}))

    def test_missing_and_existing_flag_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            flag_path = Path(directory) / "job.flag"
            environ = {CANCEL_FLAG_ENV: str(flag_path)}
            self.assertFalse(cancellation_requested(environ))
            flag_path.write_text("cancelled", encoding="utf-8")
            self.assertTrue(cancellation_requested(environ))

    def test_blank_env_value_is_ignored(self) -> None:
        self.assertFalse(cancellation_requested({CANCEL_FLAG_ENV: "   "}))


class TrackingCancelCheckpointTests(unittest.TestCase):
    """取消只在整行边界生效：哨兵存在时一行都不再处理，并返回取消退出码。"""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_cancel_flag_stops_before_the_first_row(self) -> None:
        import sync_shipped_tracking as tracking

        flag_path = self.root / "cancel.flag"
        flag_path.write_text("cancelled", encoding="utf-8")
        shipped_row = {
            "creator_name": "prettybalanced_",
            "creator_id": "7494176038218335933",
            "apply_id": "8071268820588204733",
            "product_id": "1732414717062320994",
            "main_order_id": "577562548345868989",
            "_shop_id": "shop-1",
        }
        with (
            patch.dict(os.environ, {CANCEL_FLAG_ENV: str(flag_path)}),
            patch.object(
                sys,
                "argv",
                ["sync_shipped_tracking.py", "--force", "--out", str(self.root / "out")],
            ),
            patch.object(tracking, "load_dotenv"),
            patch.object(tracking, "configure_logging"),
            patch.object(tracking, "set_verbose"),
            patch.object(tracking, "resolve_store_id", return_value="store-1"),
            patch.object(
                tracking,
                "load_hero_from_feishu",
                side_effect=tracking.FeishuHeroError("no hero"),
            ),
            patch.object(
                tracking,
                "load_bitable_settings",
                return_value={"app_token": "app", "table_id": "tbl"},
            ),
            patch.object(tracking, "get_bitable_access_token", return_value="token"),
            patch.object(tracking, "list_sample_product_options", return_value=["B005"]),
            patch.object(
                tracking,
                "search_pending_ship_records",
                return_value=[{"record_id": "rec-1"}],
            ),
            patch.object(
                tracking,
                "index_pending_ship_records",
                return_value={"lookup-key": {"record_id": "rec-1", "fields": {}}},
            ),
            patch.object(tracking, "pending_ship_lookup_key", return_value="lookup-key"),
            patch.object(
                tracking,
                "resolve_sample_product_for_row",
                return_value={"ok": True, "option": "B005", "sku": "B005"},
            ),
            patch.object(tracking, "ensure_sample_page_loaded"),
            patch.object(tracking, "scrape_shipped_list_api", return_value=[shipped_row]),
            patch.object(tracking, "write_generic_reports", return_value={}),
            patch.object(tracking, "format_job_summary", return_value="summary"),
            patch.object(tracking, "fetch_tiktok_tracking_api") as fetch_tracking,
        ):
            return_code = tracking.main()

        self.assertEqual(return_code, EXIT_CODE_CANCELLED)
        fetch_tracking.assert_not_called()
