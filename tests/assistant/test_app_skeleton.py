from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import AppSetting, Base, set_setting
from assistant.lifecycle import InstanceLock, _is_process_alive, run_assistant


class ApplicationSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.app = create_app(port=8765)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_health_is_public_and_contains_no_secret(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertNotIn("secret", response.text.lower())

    def test_home_renders_without_bootstrap(self) -> None:
        with patch("lib.zclaw.list_running_stores", return_value=[]):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ZnSampleAssistant", response.text)
        self.assertNotIn('action="/api/jobs/daily-refresh"', response.text)
        self.assertNotIn("推荐操作", response.text)
        self.assertNotIn("单独操作", response.text)
        self.assertNotIn("0–3 脚本", response.text)
        self.assertIn("运行前准备", response.text)
        self.assertIn("运行 1–3", response.text)
        self.assertIn("Part.B: 追踪物流，跟进达人视频发布", response.text)
        self.assertIn('action="/api/jobs/operator/prepare"', response.text)
        self.assertIn("data-preparation-status", response.text)
        self.assertIn("data-store-prepare-button", response.text)
        self.assertIn('action="/api/jobs/operator/screen"', response.text)
        self.assertIn('action="/api/jobs/operator/pipeline"', response.text)
        self.assertIn('action="/api/jobs/operator/tracking"', response.text)
        self.assertLess(
            response.text.index('action="/api/jobs/operator/prepare"'),
            response.text.index("Part.A: 自动批准样品申请"),
        )
        self.assertLess(
            response.text.index('action="/api/jobs/operator/tracking"'),
            response.text.index('action="/api/jobs/shipment-sync"'),
        )
        self.assertIn('<details class="environment-details">', response.text)
        self.assertNotIn('<details class="environment-details" open>', response.text)
        self.assertIn('data-confirm-token="y"', response.text)
        self.assertIn("/static/app.js", response.text)
        self.assertIn('data-theme="light"', response.text)

    def test_preparation_status_reports_ready_execute_script_channel(self) -> None:
        store = {"storeId": "store-custom", "storeName": "Custom"}
        with (
            patch("lib.zclaw.list_running_stores", return_value=[store]),
            patch(
                "lib.zclaw.probe_store_page",
                return_value={"ready": "complete"},
            ) as probe_store_page,
        ):
            response = self.client.get("/api/stores/preparation-status")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["debug_ready"])
        self.assertEqual(response.json()["debug_status"], "ready")
        probe_store_page.assert_called_once_with(
            "store-custom",
            retries=0,
            timeout=5,
        )

    def test_preparation_status_reports_open_store_without_debug_channel(self) -> None:
        store = {"storeId": "store-custom", "storeName": "Custom"}
        with (
            patch("lib.zclaw.list_running_stores", return_value=[store]),
            patch(
                "lib.zclaw.probe_store_page",
                side_effect=RuntimeError("debug channel unavailable"),
            ),
        ):
            response = self.client.get("/api/stores/preparation-status")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertFalse(response.json()["debug_ready"])
        self.assertEqual(response.json()["debug_status"], "not-ready")
        self.assertNotIn("debug channel unavailable", response.text)

    def test_preparation_status_does_not_probe_without_unique_store(self) -> None:
        with (
            patch("lib.zclaw.list_running_stores", return_value=[]),
            patch("lib.zclaw.probe_store_page") as probe_store_page,
        ):
            response = self.client.get("/api/stores/preparation-status")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["debug_ready"])
        self.assertEqual(response.json()["error"], "running-not-unique")
        probe_store_page.assert_not_called()

    def test_stores_never_choose_a_default(self) -> None:
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
        with (
            patch("lib.zclaw.list_running_stores", return_value=[]) as list_stores,
            patch("lib.zclaw_cli.resolve_ziniao_cli_command", return_value=["node", "run.js"]),
            patch("lib.app_config.resolve_config_path", return_value=Path("config.toml")),
            patch(
                "lib.app_config.load_raw_config",
                return_value={"feishu": {"app_secret": "must-not-render"}},
            ),
        ):
            response = self.client.get("/diagnostics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<dt>Bridge</dt><dd>READY</dd>", response.text)
        self.assertNotIn("must-not-render", response.text)
        self.assertNotIn("app_secret", response.text)
        list_stores.assert_called_once_with()

    def test_diagnostics_reports_bridge_unavailable_without_retrying(self) -> None:
        with (
            patch(
                "lib.zclaw.list_running_stores",
                side_effect=RuntimeError("bridge unavailable"),
            ) as list_stores,
            patch("lib.zclaw_cli.resolve_ziniao_cli_command", return_value=["node", "run.js"]),
            patch("lib.app_config.resolve_config_path", return_value=None),
        ):
            response = self.client.get("/diagnostics")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "<dt>Bridge</dt><dd>BRIDGE_UNAVAILABLE</dd>", response.text
        )
        list_stores.assert_called_once_with()

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

    def test_windows_lock_records_pid_and_releases_owned_file(self) -> None:
        lock_path = self.root / "runtime" / "instance.lock"
        with (
            patch("assistant.lifecycle.sys.platform", "win32"),
            patch("assistant.lifecycle.os.getpid", return_value=4123),
        ):
            instance_lock = InstanceLock(lock_path)
            self.assertTrue(instance_lock.acquire())
            self.assertEqual(lock_path.read_text(encoding="ascii"), "4123\n")
            instance_lock.release()
        self.assertFalse(lock_path.exists())

    def test_windows_lock_reclaims_confirmed_orphan_and_retries_atomically(self) -> None:
        lock_path = self.root / "runtime" / "instance.lock"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text("987654\n", encoding="ascii")
        with (
            patch("assistant.lifecycle.sys.platform", "win32"),
            patch("assistant.lifecycle._is_process_alive", return_value=False) as is_alive,
            patch("assistant.lifecycle.os.getpid", return_value=4123),
        ):
            instance_lock = InstanceLock(lock_path)
            self.assertTrue(instance_lock.acquire())
            is_alive.assert_called_once_with(987654)
            self.assertEqual(lock_path.read_text(encoding="ascii"), "4123\n")
            instance_lock.release()
        self.assertFalse(lock_path.exists())

    def test_windows_lock_does_not_reclaim_live_or_unknown_owner(self) -> None:
        for owner_status in (True, None):
            with self.subTest(owner_status=owner_status):
                lock_path = self.root / f"runtime-{owner_status}" / "instance.lock"
                lock_path.parent.mkdir(parents=True)
                lock_path.write_text("987654\n", encoding="ascii")
                with (
                    patch("assistant.lifecycle.sys.platform", "win32"),
                    patch(
                        "assistant.lifecycle._is_process_alive",
                        return_value=owner_status,
                    ) as is_alive,
                ):
                    instance_lock = InstanceLock(lock_path)
                    self.assertFalse(instance_lock.acquire())
                    is_alive.assert_called_once_with(987654)
                self.assertTrue(lock_path.exists())

    def test_windows_lock_rejects_untrusted_owner_format(self) -> None:
        for lock_contents in ("not-a-pid", "0\n", "123\nextra"):
            with self.subTest(lock_contents=lock_contents):
                lock_path = self.root / f"runtime-{hash(lock_contents)}" / "instance.lock"
                lock_path.parent.mkdir(parents=True)
                lock_path.write_text(lock_contents, encoding="ascii")
                with (
                    patch("assistant.lifecycle.sys.platform", "win32"),
                    patch("assistant.lifecycle._is_process_alive") as is_alive,
                ):
                    instance_lock = InstanceLock(lock_path)
                    self.assertFalse(instance_lock.acquire())
                    is_alive.assert_not_called()
                self.assertTrue(lock_path.exists())

    def test_process_liveness_fails_closed_on_permission_error(self) -> None:
        with patch(
            "assistant.lifecycle.os.kill",
            side_effect=PermissionError,
        ):
            self.assertIsNone(_is_process_alive(987654))

    def test_windows_release_does_not_delete_replacement_lock(self) -> None:
        lock_path = self.root / "runtime" / "instance.lock"
        with (
            patch("assistant.lifecycle.sys.platform", "win32"),
            patch("assistant.lifecycle.os.getpid", return_value=4123),
        ):
            instance_lock = InstanceLock(lock_path)
            self.assertTrue(instance_lock.acquire())
            lock_path.unlink()
            lock_path.write_text("987654\n", encoding="ascii")
            instance_lock.release()
        self.assertTrue(lock_path.exists())
        self.assertEqual(lock_path.read_text(encoding="ascii"), "987654\n")

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
        self.assertFalse((self.root / "runtime" / "state.json").exists())

    def test_lifecycle_releases_created_resources_when_worker_start_fails(self) -> None:
        application = MagicMock()
        database_engine = MagicMock()
        session_factory = object()
        with (
            patch("assistant.lifecycle.sys.platform", "win32"),
            patch("assistant.lifecycle.ensure_user_dirs", return_value=self.root),
            patch("assistant.lifecycle.choose_available_port", return_value=8765),
            patch("assistant.lifecycle.create_app", return_value=application),
            patch("assistant.lifecycle.create_database_engine", return_value=database_engine),
            patch("assistant.lifecycle.Base.metadata.create_all"),
            patch("assistant.lifecycle.sessionmaker", return_value=session_factory),
            patch("assistant.lifecycle.install_ziniao_busy_guard"),
            patch(
                "assistant.lifecycle.start_worker",
                side_effect=RuntimeError("worker-start-failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "worker-start-failed"):
                run_assistant(uvicorn_runner=MagicMock())
        database_engine.dispose.assert_called_once_with()
        self.assertFalse((self.root / "runtime" / "instance.lock").exists())
        self.assertFalse((self.root / "runtime" / "state.json").exists())

    def test_lifecycle_releases_resources_when_state_write_fails(self) -> None:
        application = MagicMock()
        database_engine = MagicMock()
        worker_controller = MagicMock()
        session_factory = object()
        with (
            patch("assistant.lifecycle.sys.platform", "win32"),
            patch("assistant.lifecycle.ensure_user_dirs", return_value=self.root),
            patch("assistant.lifecycle.choose_available_port", return_value=8765),
            patch("assistant.lifecycle.create_app", return_value=application),
            patch("assistant.lifecycle.create_database_engine", return_value=database_engine),
            patch("assistant.lifecycle.Base.metadata.create_all"),
            patch("assistant.lifecycle.sessionmaker", return_value=session_factory),
            patch("assistant.lifecycle.install_ziniao_busy_guard"),
            patch("assistant.lifecycle.start_worker", return_value=worker_controller),
            patch("assistant.lifecycle.Path.write_text", side_effect=OSError("state-write-failed")),
        ):
            with self.assertRaisesRegex(OSError, "state-write-failed"):
                run_assistant(uvicorn_runner=MagicMock())
        worker_controller.stop.assert_called_once_with(timeout=5.0)
        database_engine.dispose.assert_called_once_with()
        self.assertFalse((self.root / "runtime" / "instance.lock").exists())
        self.assertFalse((self.root / "runtime" / "state.json").exists())

    def test_lifecycle_releases_lock_when_app_creation_fails(self) -> None:
        with (
            patch("assistant.lifecycle.sys.platform", "win32"),
            patch("assistant.lifecycle.ensure_user_dirs", return_value=self.root),
            patch("assistant.lifecycle.choose_available_port", return_value=8765),
            patch(
                "assistant.lifecycle.create_app",
                side_effect=RuntimeError("app-create-failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "app-create-failed"):
                run_assistant(uvicorn_runner=MagicMock())
        self.assertFalse((self.root / "runtime" / "instance.lock").exists())
        self.assertFalse((self.root / "runtime" / "state.json").exists())

    def test_cleanup_failure_does_not_prevent_later_cleanup_steps(self) -> None:
        application = MagicMock()
        database_engine = MagicMock()
        worker_controller = MagicMock()
        worker_controller.stop.side_effect = RuntimeError("worker-stop-failed")
        database_engine.dispose.side_effect = RuntimeError("engine-dispose-failed")
        session_factory = object()
        with (
            patch("assistant.lifecycle.sys.platform", "win32"),
            patch("assistant.lifecycle.ensure_user_dirs", return_value=self.root),
            patch("assistant.lifecycle.choose_available_port", return_value=8765),
            patch("assistant.lifecycle.create_app", return_value=application),
            patch("assistant.lifecycle.create_database_engine", return_value=database_engine),
            patch("assistant.lifecycle.Base.metadata.create_all"),
            patch("assistant.lifecycle.sessionmaker", return_value=session_factory),
            patch("assistant.lifecycle.install_ziniao_busy_guard"),
            patch("assistant.lifecycle.start_worker", return_value=worker_controller),
            patch("assistant.lifecycle.webbrowser.open"),
            patch("assistant.lifecycle.logger.exception"),
        ):
            self.assertEqual(run_assistant(uvicorn_runner=MagicMock()), 0)
        worker_controller.stop.assert_called_once_with(timeout=5.0)
        database_engine.dispose.assert_called_once_with()
        self.assertFalse((self.root / "runtime" / "instance.lock").exists())
        self.assertFalse((self.root / "runtime" / "state.json").exists())

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
