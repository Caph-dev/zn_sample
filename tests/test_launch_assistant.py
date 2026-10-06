from __future__ import annotations

import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import launch_assistant  # noqa: E402


class ExistingInstanceDetectionTests(unittest.TestCase):
    def test_no_state_returns_none(self) -> None:
        self.assertIsNone(launch_assistant._existing_instance(None))
        self.assertIsNone(launch_assistant._existing_instance({}))

    def test_dead_pid_returns_none(self) -> None:
        with (
            patch.object(launch_assistant, "_is_process_alive", return_value=False),
            patch.object(launch_assistant, "_health_check", return_value=True),
        ):
            self.assertIsNone(
                launch_assistant._existing_instance({"pid": 123, "port": 8765})
            )

    def test_unhealthy_port_returns_none(self) -> None:
        with (
            patch.object(launch_assistant, "_is_process_alive", return_value=True),
            patch.object(launch_assistant, "_health_check", return_value=False),
        ):
            self.assertIsNone(
                launch_assistant._existing_instance({"pid": 123, "port": 8765})
            )

    def test_alive_and_healthy_returns_pid_and_port(self) -> None:
        with (
            patch.object(launch_assistant, "_is_process_alive", return_value=True),
            patch.object(launch_assistant, "_health_check", return_value=True),
        ):
            self.assertEqual(
                launch_assistant._existing_instance({"pid": 123, "port": 8765}),
                (123, 8765),
            )


class TerminateProcessTests(unittest.TestCase):
    def test_missing_pid_returns_true(self) -> None:
        with patch.object(launch_assistant, "_is_process_alive", return_value=False):
            self.assertTrue(launch_assistant._terminate_process(999999))

    @unittest.skipIf(sys.platform == "win32", "POSIX 强杀升级路径")
    @patch("launch_assistant.time.sleep")
    @patch("launch_assistant.time.monotonic")
    @patch("launch_assistant.os.kill")
    def test_escalates_to_sigkill_when_sigterm_not_enough(
        self, kill_mock, monotonic, _sleep
    ) -> None:
        clock = {"now": 1000.0}

        def tick():
            clock["now"] += 0.5
            return clock["now"]

        monotonic.side_effect = tick
        with patch.object(launch_assistant, "_is_process_alive", return_value=True):
            self.assertFalse(launch_assistant._terminate_process(123))

        kill_mock.assert_any_call(123, signal.SIGTERM)
        kill_mock.assert_any_call(123, signal.SIGKILL)


class MainFlowTests(unittest.TestCase):
    @patch("launch_assistant.bootstrap.main")
    @patch("launch_assistant.ensure_user_dirs")
    def test_no_existing_instance_starts_directly(self, _ensure_dirs, bootstrap_main) -> None:
        with (
            patch.object(launch_assistant, "_read_runtime_state", return_value=None),
            patch.object(launch_assistant, "_existing_instance", return_value=None),
            patch("sys.argv", ["launch_assistant.py"]),
        ):
            self.assertEqual(launch_assistant.main(), bootstrap_main.return_value)
        bootstrap_main.assert_called_once()

    @patch("launch_assistant.bootstrap.main")
    @patch("launch_assistant.ensure_user_dirs")
    def test_no_restart_flag_skips_kill(self, _ensure_dirs, bootstrap_main) -> None:
        with (
            patch.object(launch_assistant, "_read_runtime_state", return_value={"pid": 123, "port": 8765}),
            patch.object(launch_assistant, "_existing_instance", return_value=(123, 8765)),
            patch.object(launch_assistant, "_terminate_process") as terminate,
            patch.object(launch_assistant, "_running_job_types", return_value=[]),
            patch("sys.argv", ["launch_assistant.py", "--no-restart"]),
        ):
            launch_assistant.main()
        terminate.assert_not_called()
        bootstrap_main.assert_called_once()

    @patch("launch_assistant.bootstrap.main")
    @patch("launch_assistant.ensure_user_dirs")
    def test_existing_instance_is_killed_and_restarted(self, _ensure_dirs, bootstrap_main) -> None:
        with (
            patch.object(launch_assistant, "_read_runtime_state", return_value={"pid": 123, "port": 8765}),
            patch.object(launch_assistant, "_existing_instance", return_value=(123, 8765)),
            patch.object(launch_assistant, "_terminate_process", return_value=True) as terminate,
            patch.object(launch_assistant, "_running_job_types", return_value=[]),
            patch.object(launch_assistant, "_mark_interrupted_running_jobs") as mark,
            patch("sys.argv", ["launch_assistant.py"]),
        ):
            launch_assistant.main()
        terminate.assert_called_once_with(123)
        mark.assert_called_once_with([])
        bootstrap_main.assert_called_once()

    @patch("launch_assistant.bootstrap.main")
    @patch("launch_assistant.ensure_user_dirs")
    def test_protected_job_blocks_restart(self, _ensure_dirs, bootstrap_main) -> None:
        with (
            patch.object(launch_assistant, "_read_runtime_state", return_value={"pid": 123, "port": 8765}),
            patch.object(launch_assistant, "_existing_instance", return_value=(123, 8765)),
            patch.object(launch_assistant, "_terminate_process") as terminate,
            patch.object(launch_assistant, "_running_job_types", return_value=["operator_pipeline"]),
            patch("sys.argv", ["launch_assistant.py"]),
        ):
            self.assertEqual(launch_assistant.main(), 2)
        terminate.assert_not_called()
        bootstrap_main.assert_not_called()

    def test_registered_cleanup_execution_in_database_blocks_source_restart(self) -> None:
        from sqlalchemy.orm import sessionmaker

        from assistant.database.engine import create_database_engine
        from assistant.database.models import Job
        from assistant.jobs.handlers.target_cleanup import run_target_cleanup_job
        from assistant.jobs.registry import get_handler

        self.assertIs(get_handler("target_cleanup_execute"), run_target_cleanup_job)
        with tempfile.TemporaryDirectory() as temporary_directory:
            sqlite_path = Path(temporary_directory) / "assistant.sqlite3"
            engine = create_database_engine(sqlite_path)
            Job.__table__.create(engine)
            session_factory = sessionmaker(bind=engine)
            try:
                with session_factory() as session:
                    session.add(Job(id="protected-cleanup", job_type="target_cleanup_execute", status="running",
                                    result_summary="preserve-known-result", error_code="preserve-checkpoint"))
                    session.commit()
                with (
                    patch.object(launch_assistant, "database_path", return_value=sqlite_path),
                    patch.object(launch_assistant, "ensure_user_dirs"),
                    patch.object(launch_assistant, "_read_runtime_state", return_value={"pid": 123, "port": 8765}),
                    patch.object(launch_assistant, "_existing_instance", return_value=(123, 8765)),
                    patch.object(launch_assistant, "_terminate_process") as terminate,
                    patch.object(launch_assistant, "_mark_interrupted_running_jobs") as mark,
                    patch.object(launch_assistant.bootstrap, "main") as bootstrap_main,
                    patch("sys.argv", ["launch_assistant.py"]),
                ):
                    self.assertEqual(launch_assistant.main(), 2)
                terminate.assert_not_called()
                mark.assert_not_called()
                bootstrap_main.assert_not_called()
                with session_factory() as session:
                    job = session.get(Job, "protected-cleanup")
                    self.assertEqual((job.status, job.result_summary, job.error_code),
                                     ("running", "preserve-known-result", "preserve-checkpoint"))
            finally:
                engine.dispose()

    @patch("launch_assistant.bootstrap.main")
    @patch("launch_assistant.ensure_user_dirs")
    def test_readonly_running_job_is_interrupted_and_marked(self, _ensure_dirs, bootstrap_main) -> None:
        with (
            patch.object(launch_assistant, "_read_runtime_state", return_value={"pid": 123, "port": 8765}),
            patch.object(launch_assistant, "_existing_instance", return_value=(123, 8765)),
            patch.object(launch_assistant, "_terminate_process", return_value=True),
            patch.object(launch_assistant, "_running_job_types", return_value=["shipment_sync"]),
            patch.object(launch_assistant, "_mark_interrupted_running_jobs") as mark,
            patch("sys.argv", ["launch_assistant.py"]),
        ):
            launch_assistant.main()
        mark.assert_called_once_with(["shipment_sync"])
        bootstrap_main.assert_called_once()

    @patch("launch_assistant.bootstrap.main")
    @patch("launch_assistant.ensure_user_dirs")
    def test_terminate_failure_returns_one(self, _ensure_dirs, bootstrap_main) -> None:
        with (
            patch.object(launch_assistant, "_read_runtime_state", return_value={"pid": 123, "port": 8765}),
            patch.object(launch_assistant, "_existing_instance", return_value=(123, 8765)),
            patch.object(launch_assistant, "_terminate_process", return_value=False),
            patch.object(launch_assistant, "_running_job_types", return_value=[]),
            patch("sys.argv", ["launch_assistant.py"]),
        ):
            self.assertEqual(launch_assistant.main(), 1)
        bootstrap_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
