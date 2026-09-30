"""config.toml [stores] 默认店铺配置：换客户不改代码。"""
from __future__ import annotations

import contextlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.app_config import load_store_settings  # noqa: E402
from lib.store_launcher import (  # noqa: E402
    DEFAULT_PREPARE_STORE_ID,
    default_prepare_store,
    resolve_store_id_for_prepare,
)
from lib.zclaw import (  # noqa: E402
    DEFAULT_TEST_STORE_ID,
    default_scan_store,
    resolve_store_id,
)

STORE_ENV_KEYS = (
    "ZN_SAMPLE_STORE_ID",
    "ZN_SAMPLE_STORE_NAME",
    "ZN_SAMPLE_PREPARE_STORE_ID",
    "ZN_SAMPLE_PREPARE_STORE_NAME",
)


@contextlib.contextmanager
def store_env(**values):
    """临时设置/清空默认店环境变量，避免测试受本机 .env 影响。"""
    saved = {key: os.environ.get(key) for key in STORE_ENV_KEYS}
    try:
        for key in STORE_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update({key: value for key, value in values.items() if value})
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class LoadStoreSettingsTests(unittest.TestCase):
    def test_reads_stores_section(self) -> None:
        raw = {
            "stores": {
                "default_store_id": "custom-scan",
                "default_store_name": "客户一店",
                "prepare_store_id": "custom-prepare",
                "prepare_store_name": "客户二店",
            }
        }
        with patch("lib.app_config.load_raw_config", return_value=raw), patch(
            "lib.app_config.resolve_config_path", return_value=None
        ), store_env():
            settings = load_store_settings()
        self.assertEqual(settings["default_store_id"], "custom-scan")
        self.assertEqual(settings["default_store_name"], "客户一店")
        self.assertEqual(settings["prepare_store_id"], "custom-prepare")
        self.assertEqual(settings["prepare_store_name"], "客户二店")

    def test_env_overrides_config_file(self) -> None:
        raw = {"stores": {"default_store_id": "from-file"}}
        with patch("lib.app_config.load_raw_config", return_value=raw), patch(
            "lib.app_config.resolve_config_path", return_value=None
        ), store_env(ZN_SAMPLE_STORE_ID="from-env"):
            settings = load_store_settings()
        self.assertEqual(settings["default_store_id"], "from-env")


class DefaultScanStoreTests(unittest.TestCase):
    @patch(
        "lib.zclaw.load_store_settings",
        return_value={"default_store_id": "custom-scan", "default_store_name": "客户一店"},
    )
    def test_config_overrides_builtin_store(self, _settings) -> None:
        store = default_scan_store()
        self.assertEqual(store["store_id"], "custom-scan")
        self.assertEqual(store["store_name"], "客户一店")

    @patch(
        "lib.zclaw.load_store_settings",
        return_value={"default_store_id": None, "default_store_name": None},
    )
    def test_falls_back_to_builtin_test_store_with_name(self, _settings) -> None:
        store = default_scan_store()
        self.assertEqual(store["store_id"], DEFAULT_TEST_STORE_ID)
        self.assertEqual(store["store_name"], "跨境1号店（Lingerie Outlet）")

    @patch(
        "lib.zclaw.load_store_settings",
        return_value={"default_store_id": "custom-scan", "default_store_name": None},
    )
    def test_configured_store_does_not_borrow_builtin_name(self, _settings) -> None:
        store = default_scan_store()
        self.assertEqual(store["store_id"], "custom-scan")
        self.assertIsNone(store["store_name"])

    @patch("lib.zclaw.load_store_settings", side_effect=AssertionError("不应读配置"))
    def test_disabled_never_reads_config(self, _settings) -> None:
        self.assertIsNone(default_scan_store(disabled=True)["store_id"])

    def test_resolve_store_id_accepts_configured_default(self) -> None:
        with patch(
            "lib.zclaw.load_store_settings",
            return_value={"default_store_id": "custom-scan", "default_store_name": "客户一店"},
        ), patch("lib.zclaw.list_running_stores", return_value=[]):
            default_sid = default_scan_store()["store_id"]
            store_id = resolve_store_id(default_store_id=default_sid)
        self.assertEqual(store_id, "custom-scan")


class DefaultPrepareStoreTests(unittest.TestCase):
    @patch(
        "lib.store_launcher.load_store_settings",
        return_value={"prepare_store_id": "custom-prepare", "prepare_store_name": "客户二店"},
    )
    def test_config_overrides_builtin_store(self, _settings) -> None:
        store = default_prepare_store()
        self.assertEqual(store["store_id"], "custom-prepare")
        self.assertEqual(store["store_name"], "客户二店")

    @patch(
        "lib.store_launcher.load_store_settings",
        return_value={"prepare_store_id": None, "prepare_store_name": None},
    )
    def test_falls_back_to_builtin_store_two(self, _settings) -> None:
        store = default_prepare_store()
        self.assertEqual(store["store_id"], DEFAULT_PREPARE_STORE_ID)
        self.assertEqual(store["store_name"], "跨境2号店")

    @patch("lib.store_launcher.load_store_settings", side_effect=AssertionError("不应读配置"))
    def test_disabled_never_reads_config(self, _settings) -> None:
        store = default_prepare_store(disabled=True)
        self.assertIsNone(store["store_id"])
        self.assertIsNone(store["store_name"])

    def test_custom_name_appears_in_missing_default_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "找不到默认的 客户二店"):
            resolve_store_id_for_prepare(
                default_store_id="custom-prepare",
                default_store_name="客户二店",
                list_running_stores_fn=lambda: [],
                list_all_stores_fn=lambda limit=50: [
                    {"storeId": "a", "storeName": "甲"},
                ],
            )

    def test_missing_name_falls_back_to_generic_label(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "找不到默认的 默认店"):
            resolve_store_id_for_prepare(
                default_store_id="custom-prepare",
                list_running_stores_fn=lambda: [],
                list_all_stores_fn=lambda limit=50: [
                    {"storeId": "a", "storeName": "甲"},
                ],
            )


if __name__ == "__main__":
    unittest.main()
