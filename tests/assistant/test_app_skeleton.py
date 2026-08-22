from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import AppSetting, Base, set_setting
from assistant.lifecycle import run_assistant


class ApplicationSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.app = create_app(runtime_directory=self.root / "runtime", port=8765)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def authenticate(self) -> None:
        token = self.app.state.session_manager.issue_bootstrap_token()
        response = self.client.get(f"/bootstrap?token={token}", follow_redirects=False)
        self.assertEqual(response.status_code, 302)

    def test_health_is_public_and_contains_no_secret(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertNotIn("secret", response.text.lower())

    def test_home_requires_session_then_renders_after_bootstrap(self) -> None:
        self.assertEqual(self.client.get("/").status_code, 401)
        self.authenticate()
        with patch("lib.zclaw.list_running_stores", return_value=[]):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ZnSampleAssistant", response.text)

    def test_stores_never_choose_a_default(self) -> None:
        self.authenticate()
        with patch(
            "lib.zclaw.list_running_stores",
            return_value=[
                {"storeId": "store-a", "storeName": "A"},
                {"storeId": "store-b", "storeName": "B"},
            ],
        ):
            response = self.client.get("/api/stores")
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "running-not-unique")

        with patch(
            "lib.zclaw.list_running_stores",
            return_value=[{"storeId": "store-custom", "storeName": "Custom"}],
        ):
            response = self.client.get("/api/stores")
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["store"]["storeId"], "store-custom")
        self.assertNotEqual(response.json()["store"]["storeId"], "27506607043054")

    def test_diagnostics_never_render_configuration_values(self) -> None:
        self.authenticate()
        with (
            patch("lib.zclaw.list_running_stores", return_value=[]),
            patch("lib.zclaw_cli.resolve_ziniao_cli_command", return_value=["node", "run.js"]),
            patch("lib.app_config.resolve_config_path", return_value=Path("config.toml")),
            patch(
                "lib.app_config.load_raw_config",
                return_value={"feishu": {"app_secret": "must-not-render"}},
            ),
        ):
            response = self.client.get("/diagnostics")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("must-not-render", response.text)
        self.assertNotIn("app_secret", response.text)

    def test_database_schema_and_sensitive_setting_guard(self) -> None:
        engine = create_database_engine(self.root / "assistant.sqlite3")
        Base.metadata.create_all(engine)
        expected_tables = {
            "stores", "sample_cases", "shipments", "shipment_snapshots",
            "followup_tasks", "jobs", "job_events", "app_settings",
        }
        self.assertEqual(set(Base.metadata.tables), expected_tables)
        with Session(engine) as session:
            with self.assertRaises(ValueError):
                set_setting(session, "access_token", "never-store")
            set_setting(session, "display_mode", "compact")
            session.commit()
            self.assertEqual(
                session.scalar(select(AppSetting.value).where(AppSetting.key == "display_mode")),
                "compact",
            )
        engine.dispose()

    def test_lifecycle_binds_only_to_loopback(self) -> None:
        with (
            patch("assistant.lifecycle.ensure_user_dirs", return_value=self.root),
            patch("assistant.lifecycle.choose_available_port", return_value=8765),
            patch("assistant.lifecycle.webbrowser.open"),
        ):
            calls = []

            def fake_uvicorn_run(application, **kwargs) -> None:
                calls.append(kwargs)

            self.assertEqual(run_assistant(uvicorn_runner=fake_uvicorn_run), 0)
        self.assertEqual(calls[0]["host"], "127.0.0.1")
        self.assertEqual(calls[0]["port"], 8765)
        self.assertFalse(calls[0]["access_log"])

    def test_second_instance_opens_only_verified_existing_application(self) -> None:
        with (
            patch("assistant.lifecycle.ensure_user_dirs", return_value=self.root),
            patch("assistant.lifecycle.InstanceLock.acquire", return_value=False),
            patch(
                "assistant.lifecycle._existing_instance_url",
                return_value="http://127.0.0.1:8765/",
            ),
            patch("assistant.lifecycle.webbrowser.open") as open_browser,
        ):
            self.assertEqual(run_assistant(), 0)
        open_browser.assert_called_once_with("http://127.0.0.1:8765/")


if __name__ == "__main__":
    unittest.main()
