"""Offline lifecycle contracts: temporary state, deterministic races, no writes abroad."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant import app, bootstrap, lifecycle, paths  # noqa: E402
from assistant.database.models import Job  # noqa: E402
from assistant.jobs.locks import create_or_get_pending_job  # noqa: E402
from assistant.jobs.registry import REGISTERED_JOB_TYPES, WRITE_JOB_TYPES  # noqa: E402
from assistant.jobs.worker import _claim_next_job  # noqa: E402
from assistant.services import release_lifecycle as release  # noqa: E402
from scripts import release_launcher as launcher  # noqa: E402
from tests.assistant.test_target_cleanup_api import (  # noqa: E402
    cleanup_environment, finish_synthetic_preview, write_synthetic_results,
)

WAIT_SECONDS = 5
INSTANCE_ID = "a" * 32
REQUEST_ID = "b" * 32
STATE = {"pid": 12345, "port": 18765, "instance_id": INSTANCE_ID}


def forbidden_boundary(*arguments, **options):
    pytest.fail("A lifecycle test reached an unmocked business, process, or remote boundary")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home_directory = tmp_path / "home"
    application_directory = home_directory / "application"
    home_directory.mkdir()
    for directory_name in ("config", "runtime", "logs", "backups"):
        (application_directory / directory_name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_directory))
    monkeypatch.setenv("USERPROFILE", str(home_directory))
    monkeypatch.setenv("LOCALAPPDATA", str(home_directory))
    monkeypatch.setenv("APPDATA", str(home_directory))
    monkeypatch.setattr(paths, "_release_manifest", lambda: None)
    for module in (paths, launcher):
        monkeypatch.setattr(module, "user_data_dir", lambda: application_directory)
        monkeypatch.setattr(module, "configuration_dir", lambda: application_directory / "config")
        monkeypatch.setattr(module, "runtime_dir", lambda: application_directory / "runtime")
        monkeypatch.setattr(module, "database_path", lambda: application_directory / "assistant.sqlite3")
        monkeypatch.setattr(module, "ensure_user_dirs", lambda: application_directory)
    monkeypatch.setattr(bootstrap, "ensure_user_dirs", lambda: application_directory)
    monkeypatch.setattr(lifecycle, "ensure_user_dirs", lambda: application_directory)
    monkeypatch.setattr(launcher.urllib.request, "build_opener", forbidden_boundary)
    monkeypatch.setattr(launcher.urllib.request, "urlopen", forbidden_boundary)
    monkeypatch.setattr(launcher.webbrowser, "open", forbidden_boundary)

    class ForbiddenProcess:
        def __init__(self, *arguments, **options):
            forbidden_boundary(*arguments, **options)

    monkeypatch.setattr(subprocess, "Popen", ForbiddenProcess)
    return application_directory


@pytest.fixture
def database(isolated_home):
    sqlite_path = isolated_home / "assistant.sqlite3"
    database_engine = create_engine(f"sqlite:///{sqlite_path}", connect_args={"check_same_thread": False})
    Job.__table__.create(database_engine)
    session_factory = sessionmaker(bind=database_engine, expire_on_commit=False)
    session_factory.release_coordinator = release.LifecycleCoordinator()
    try:
        yield sqlite_path, database_engine, session_factory
    finally:
        database_engine.dispose()


class ThreadResult:
    """Propagate background exceptions; every wait is bounded, never a sleep."""

    def __init__(self, name, callback):
        self.result = None
        self.error = None
        self.done = threading.Event()

        def invoke():
            try:
                self.result = callback()
            except BaseException as error:
                self.error = error
            finally:
                self.done.set()

        self.thread = threading.Thread(target=invoke, name=name, daemon=True)
        self.thread.start()

    def join(self):
        self.thread.join(WAIT_SECONDS)
        assert not self.thread.is_alive(), f"{self.thread.name} did not finish"
        if self.error is not None:
            raise self.error
        return self.result


class ObservedLock:
    """Signal proven contention, rather than relying on thread scheduling."""

    def __init__(self):
        self.lock = threading.RLock()
        self.stop_contended = threading.Event()

    def __enter__(self):
        if threading.current_thread().name == "idle-stop":
            if self.lock.acquire(blocking=False):
                return self
            self.stop_contended.set()
        self.lock.acquire()
        return self

    def __exit__(self, *exception):
        self.lock.release()


@pytest.mark.parametrize("operation", ("enqueue", "claim"))
def test_stop_serializes_with_enqueue_and_claim_through_commit(database, operation):
    _, database_engine, session_factory = database
    coordinator = session_factory.release_coordinator
    coordinator.lock = ObservedLock()
    if operation == "claim":
        with session_factory() as session:
            session.add(Job(id="candidate", job_type="operator_pipeline", status="pending"))
            session.commit()
    commit_reached = threading.Event()
    release_commit = threading.Event()

    def hold_commit(*arguments):
        if threading.current_thread().name == "job-operation":
            commit_reached.set()
            assert release_commit.wait(WAIT_SECONDS), "Commit gate was not released"

    event_target = session_factory if operation == "enqueue" else database_engine
    event_name = "before_commit" if operation == "enqueue" else "commit"
    event.listen(event_target, event_name, hold_commit)

    def perform_operation():
        if operation == "enqueue":
            return create_or_get_pending_job(session_factory, job_type="operator_pipeline", store_id=None)[0]
        return _claim_next_job(session_factory)

    job_thread = ThreadResult("job-operation", perform_operation)
    stop_thread = None
    try:
        assert commit_reached.wait(WAIT_SECONDS)
        stop_thread = ThreadResult("idle-stop", lambda: coordinator.request_idle_stop(session_factory))
        assert coordinator.lock.stop_contended.wait(WAIT_SECONDS)
        assert not stop_thread.done.is_set()
        assert coordinator.stopping is False
    finally:
        release_commit.set()
        job_id = job_thread.join()
        if stop_thread is not None:
            assert stop_thread.join() == "service-busy"
        event.remove(event_target, event_name, hold_commit)
    with session_factory() as session:
        assert session.get(Job, job_id).status == ("pending" if operation == "enqueue" else "running")
    assert coordinator.stopping is False


def test_stop_winner_blocks_later_enqueue_and_claim(database):
    _, _, session_factory = database
    coordinator = session_factory.release_coordinator
    assert coordinator.request_idle_stop(session_factory) == "accepted"
    with pytest.raises(release.ServiceStopping):
        create_or_get_pending_job(session_factory, job_type="report_export", store_id=None)
    assert _claim_next_job(session_factory) is None
    assert coordinator.request_idle_stop(session_factory) == "service-stopping"
    with session_factory() as session:
        assert session.scalar(select(Job.id)) is None


def test_active_operation_denies_stop_until_it_returns(database):
    _, _, session_factory = database
    coordinator = session_factory.release_coordinator
    with coordinator.operation():
        assert coordinator.active_operations == 1
        assert coordinator.request_idle_stop(session_factory) == "service-busy"
    assert coordinator.active_operations == 0
    assert coordinator.request_idle_stop(session_factory) == "accepted"


def test_inprocess_handler_stays_busy_even_if_durable_status_becomes_terminal(database, monkeypatch):
    from assistant.jobs import worker as job_worker

    _, _, session_factory = database
    job_id, _ = create_or_get_pending_job(session_factory, job_type="report_export", store_id=None)
    handler_entered = threading.Event()
    allow_return = threading.Event()

    def synthetic_handler(active_job_id, active_session_factory):
        with active_session_factory() as session:
            session.get(Job, active_job_id).status = "interrupted"
            session.commit()
        handler_entered.set()
        assert allow_return.wait(WAIT_SECONDS)
        return "synthetic-local-result"

    monkeypatch.setattr(job_worker, "get_handler", lambda job_type: synthetic_handler)
    monkeypatch.setattr(job_worker, "append_event", lambda *arguments, **options: None)
    worker_thread = ThreadResult("inprocess-worker", lambda: job_worker.worker_loop_once(session_factory))
    try:
        assert handler_entered.wait(WAIT_SECONDS)
        assert session_factory.release_coordinator.request_idle_stop(session_factory) == "service-busy"
        assert not worker_thread.done.is_set()
    finally:
        allow_return.set()
        assert worker_thread.join() == job_id
    assert session_factory.release_coordinator.request_idle_stop(session_factory) == "accepted"


@pytest.mark.parametrize("job_type", sorted(REGISTERED_JOB_TYPES | WRITE_JOB_TYPES | {"future-unknown-job"}))
@pytest.mark.parametrize("status", ("pending", "running"))
def test_all_residual_unfinished_jobs_block_without_rewriting(database, job_type, status):
    sqlite_path, _, session_factory = database
    with session_factory() as session:
        session.add(Job(id="residual", job_type=job_type, status=status,
                        result_summary="preserve-report", error_code="preserve-checkpoint"))
        session.commit()
    before = sqlite_path.read_bytes()
    with pytest.raises(release.ReleaseLifecycleError, match="^residual-jobs-require-review$"):
        release.check_residual_jobs(sqlite_path)
    assert session_factory.release_coordinator.request_idle_stop(session_factory) == "service-busy"
    assert session_factory.release_coordinator.stopping is False
    assert sqlite_path.read_bytes() == before
    with session_factory() as session:
        residual_job = session.get(Job, "residual")
        assert (residual_job.status, residual_job.result_summary, residual_job.error_code) == (
            status, "preserve-report", "preserve-checkpoint")


@pytest.mark.parametrize("status", ("succeeded", "failed", "cancelled", "interrupted"))
def test_terminal_jobs_do_not_block_stop_or_residual_check(database, status):
    sqlite_path, _, session_factory = database
    with session_factory() as session:
        session.add(Job(id="finished", job_type="operator_pipeline", status=status))
        session.commit()
    release.check_residual_jobs(sqlite_path)
    assert session_factory.release_coordinator.request_idle_stop(session_factory) == "accepted"


def test_sql_failure_denies_stop_and_does_not_set_stopping(database):
    _, database_engine, session_factory = database

    def fail_query(*arguments):
        raise sqlite3.OperationalError("synthetic SQL failure")

    event.listen(database_engine, "before_cursor_execute", fail_query)
    try:
        assert session_factory.release_coordinator.request_idle_stop(session_factory) == "database-unknown"
        assert session_factory.release_coordinator.stopping is False
    finally:
        event.remove(database_engine, "before_cursor_execute", fail_query)
    assert session_factory.release_coordinator.request_idle_stop(session_factory) == "accepted"


def test_corrupt_residual_database_fails_closed_without_repair(isolated_home):
    sqlite_path = isolated_home / "assistant.sqlite3"
    sqlite_path.write_bytes(b"not a SQLite database")
    with pytest.raises(release.ReleaseLifecycleError, match="^database-unknown$"):
        release.check_residual_jobs(sqlite_path)
    assert sqlite_path.read_bytes() == b"not a SQLite database"


def test_crashed_subprocess_marker_blocks_start_without_deleting_it(isolated_home):
    marker_path = isolated_home / "runtime" / "children" / "unknown-child.json"
    release.write_private_json(marker_path, {"state": "spawning", "owner_pid": 12345})
    before = marker_path.read_bytes()
    with pytest.raises(release.ReleaseLifecycleError, match="^residual-subprocess-requires-review$"):
        release.check_residual_jobs(isolated_home / "assistant.sqlite3")
    assert marker_path.read_bytes() == before


@pytest.fixture
def guarded_application(monkeypatch):
    monkeypatch.setattr(app, "application_version", lambda: "synthetic-release")
    application = app.create_app(port=18765)
    handler = Mock(return_value={"ok": True})

    @application.get("/lifecycle-probe")
    def read_probe():
        return handler()

    @application.post("/lifecycle-probe")
    def write_probe():
        return handler()

    coordinator = release.LifecycleCoordinator()
    release.install_release_guard(application, coordinator)
    return application, coordinator, handler


@pytest.mark.parametrize("stopping", (False, True))
@pytest.mark.parametrize("method,headers,status,detail", (
    ("GET", {"host": "attacker.invalid"}, 400, "invalid-host"),
    ("POST", {}, 403, "invalid-origin"),
    ("POST", {"origin": "https://attacker.invalid"}, 403, "invalid-origin"),
    ("POST", {"origin": "http://127.0.0.1:18766"}, 403, "invalid-origin"),
))
def test_release_admission_preserves_host_and_origin_security(
    guarded_application, stopping, method, headers, status, detail,
):
    application, coordinator, handler = guarded_application
    coordinator.stopping = stopping
    with TestClient(application, base_url="http://127.0.0.1:18765") as client:
        response = client.request(method, "/lifecycle-probe", headers=headers)
        assert (response.status_code, response.json()) == (status, {"detail": detail})
    handler.assert_not_called()
    assert coordinator.active_operations == 0


@pytest.mark.parametrize("stopping", (False, True))
def test_release_admission_returns_503_but_keeps_secure_health_available(guarded_application, stopping):
    application, coordinator, handler = guarded_application
    coordinator.stopping = stopping
    with TestClient(application, base_url="http://127.0.0.1:18765") as client:
        response = client.post("/lifecycle-probe", headers={"origin": "http://127.0.0.1:18765"})
        assert response.status_code == (503 if stopping else 200)
        if stopping:
            assert response.json() == {"detail": "service-stopping"}
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health", headers={"host": "attacker.invalid"}).status_code == 400
    assert handler.call_count == (0 if stopping else 1)
    assert coordinator.active_operations == 0


@pytest.fixture
def monitor(database, isolated_home):
    _, _, session_factory = database
    shutdown = Mock()
    control_monitor = release.ControlMonitor(
        isolated_home / "runtime" / "control", INSTANCE_ID,
        session_factory.release_coordinator, session_factory, shutdown,
    )
    return control_monitor, shutdown


@pytest.mark.parametrize("payload", (
    {"action": "stop", "instance_id": "c" * 32, "request_id": REQUEST_ID},
    {"action": "stop", "instance_id": INSTANCE_ID, "request_id": "c" * 32},
    {"action": "restart", "instance_id": INSTANCE_ID, "request_id": REQUEST_ID},
    {"action": "stop", "instance_id": INSTANCE_ID},
    {"action": "stop", "instance_id": INSTANCE_ID, "request_id": REQUEST_ID, "extra": True},
    [],
    None,
    "malformed-json",
))
def test_control_monitor_rejects_wrong_identity_and_malformed_requests(monitor, payload):
    control_monitor, shutdown = monitor
    request_path = control_monitor.directory / f"request-{REQUEST_ID}.json"
    request_path.write_text("{" if payload == "malformed-json" else json.dumps(payload), encoding="utf-8")
    assert control_monitor.process_request(request_path) == "invalid-request"
    response = release.read_private_json(control_monitor.directory / f"response-{REQUEST_ID}.json")
    assert response == {"instance_id": INSTANCE_ID, "request_id": REQUEST_ID, "result": "invalid-request"}
    assert not request_path.exists()
    assert control_monitor.coordinator.stopping is False
    shutdown.assert_not_called()


def test_control_monitor_rejects_invalid_filename_and_oversized_request(monitor):
    control_monitor, shutdown = monitor
    invalid_path = control_monitor.directory / "request-not-an-id.json"
    invalid_path.write_text("{}", encoding="utf-8")
    assert control_monitor.process_request(invalid_path) == "invalid-request"
    assert not (control_monitor.directory / "response-not-an-id.json").exists()
    oversized_path = control_monitor.directory / f"request-{REQUEST_ID}.json"
    oversized_path.write_text(" " * 16385, encoding="utf-8")
    assert control_monitor.process_request(oversized_path) == "invalid-request"
    shutdown.assert_not_called()


def test_control_monitor_accepts_only_once_and_replay_never_requests_shutdown(monitor):
    control_monitor, shutdown = monitor
    request_path = control_monitor.directory / f"request-{REQUEST_ID}.json"
    payload = {"action": "stop", "instance_id": INSTANCE_ID, "request_id": REQUEST_ID}
    release.write_private_json(request_path, payload)
    assert control_monitor.process_request(request_path) == "accepted"
    shutdown.assert_called_once_with()
    release.write_private_json(request_path, payload)
    assert control_monitor.process_request(request_path) == "invalid-request"
    shutdown.assert_called_once_with()


def test_busy_control_request_does_not_become_valid_after_replay(monitor):
    control_monitor, shutdown = monitor
    request_path = control_monitor.directory / f"request-{REQUEST_ID}.json"
    payload = {"action": "stop", "instance_id": INSTANCE_ID, "request_id": REQUEST_ID}
    with control_monitor.coordinator.operation():
        release.write_private_json(request_path, payload)
        assert control_monitor.process_request(request_path) == "service-busy"
    release.write_private_json(request_path, payload)
    assert control_monitor.process_request(request_path) == "invalid-request"
    shutdown.assert_not_called()


def test_subprocess_registry_blocks_stop_and_holds_scope_until_actual_exit(database, isolated_home, monkeypatch):
    _, _, session_factory = database
    registry = release.SubprocessRegistry(isolated_home / "runtime" / "children")
    session_factory.release_coordinator.subprocess_registry = registry
    child_started = threading.Event()
    wait_entered = threading.Event()
    actual_exit = threading.Event()
    children = []

    class ControlledProcess:
        def __init__(self, arguments, **options):
            self.pid = 23456
            self.arguments = arguments
            self.options = options
            self.kill = Mock(side_effect=forbidden_boundary)
            self.terminate = Mock(side_effect=forbidden_boundary)
            children.append(self)

        def poll(self):
            return 0 if actual_exit.is_set() else None

        def wait(self, timeout=None):
            assert timeout is None, "Registry must not treat a timeout as an exit"
            wait_entered.set()
            assert actual_exit.wait(WAIT_SECONDS)
            return 0

    monkeypatch.setattr(subprocess, "Popen", ControlledProcess)

    def handler():
        with registry.track():
            subprocess.Popen(["synthetic-child", "unchanged-argument"], shell=False)
            child_started.set()
            raise RuntimeError("handler failed while child remained alive")

    handler_thread = ThreadResult("handler-with-child", handler)
    try:
        assert child_started.wait(WAIT_SECONDS)
        assert wait_entered.wait(WAIT_SECONDS)
        assert registry.has_active_children()
        assert session_factory.release_coordinator.request_idle_stop(session_factory) == "service-busy"
        assert not handler_thread.done.is_set()
        assert subprocess.Popen is not ControlledProcess
        marker_paths = list(registry.directory.glob("*.json"))
        assert len(marker_paths) == 1
        marker = release.read_private_json(marker_paths[0])
        assert marker["state"] == "started" and marker["pid"] == children[0].pid
        assert "arguments" not in marker and "options" not in marker
        assert children[0].arguments == ["synthetic-child", "unchanged-argument"]
        assert children[0].options == {"shell": False}
    finally:
        actual_exit.set()
        with pytest.raises(RuntimeError, match="handler failed"):
            handler_thread.join()
    assert subprocess.Popen is ControlledProcess
    assert not registry.has_active_children()
    assert list(registry.directory.glob("*.json")) == []
    assert session_factory.release_coordinator.request_idle_stop(session_factory) == "accepted"
    children[0].kill.assert_not_called()
    children[0].terminate.assert_not_called()


def test_failed_subprocess_spawn_removes_marker(isolated_home, monkeypatch):
    class FailedProcess:
        def __init__(self, *arguments, **options):
            raise OSError("synthetic spawn failure")

    monkeypatch.setattr(subprocess, "Popen", FailedProcess)
    registry = release.SubprocessRegistry(isolated_home / "runtime" / "children")
    with pytest.raises(OSError, match="synthetic spawn failure"):
        with registry.track():
            subprocess.Popen(["synthetic-child"])
    assert not registry.has_active_children()
    assert list(registry.directory.glob("*.json")) == []
    assert subprocess.Popen is FailedProcess


def test_registered_cleanup_execution_protects_source_restart_and_release_child_lifetime(cleanup_environment, monkeypatch):
    import launch_assistant
    from assistant.jobs import worker as job_worker
    from assistant.jobs.handlers.target_cleanup import run_target_cleanup_job
    from assistant.jobs.registry import get_handler
    from assistant.services import target_cleanup_service as cleanup_service

    environment = cleanup_environment
    session_factory = environment.session_factory
    preview = cleanup_service.create_preview(session_factory, 4, "preview")
    finish_synthetic_preview(environment, preview)
    execution = cleanup_service.create_execution(session_factory, preview["batch_id"], "y", "execution")
    assert get_handler("target_cleanup_execute") is run_target_cleanup_job
    coordinator = release.LifecycleCoordinator()
    session_factory.release_coordinator = coordinator
    registry = release.SubprocessRegistry(environment.root / "runtime" / "children")
    registry.coordinator = coordinator
    coordinator.subprocess_registry = registry
    child_entered = threading.Event()
    allow_exit = threading.Event()
    children = []

    class ControlledCleanupProcess:
        def __init__(self, arguments, **options):
            self.pid = 23456
            self.returncode = None
            self.args = arguments
            self.arguments = arguments
            self.options = options
            self.kill = Mock(side_effect=forbidden_boundary)
            self.terminate = Mock(side_effect=forbidden_boundary)
            children.append(self)
            cleanup_service.begin_script(session_factory, execution["batch_id"], execution["job_id"], "execute")

        def __enter__(self):
            return self

        def __exit__(self, *exception):
            self.wait()

        def communicate(self, input=None, timeout=None):
            child_entered.set()
            assert allow_exit.wait(WAIT_SECONDS), "Cleanup child gate was not released"
            write_synthetic_results(environment, execution["batch_id"], ("submitted", "submitted"))
            self.returncode = 0
            return None, None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            assert allow_exit.wait(WAIT_SECONDS)
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", ControlledCleanupProcess)
    monkeypatch.setattr(launch_assistant, "database_path", lambda: environment.root / "assistant.sqlite3")
    monkeypatch.setattr(launch_assistant, "ensure_user_dirs", lambda: environment.root)
    monkeypatch.setattr(launch_assistant, "_read_runtime_state", lambda: STATE)
    monkeypatch.setattr(launch_assistant, "_existing_instance", lambda state: (STATE["pid"], STATE["port"]))
    terminate = Mock(side_effect=forbidden_boundary)
    mark_interrupted = Mock(side_effect=forbidden_boundary)
    startup = Mock(side_effect=forbidden_boundary)
    monkeypatch.setattr(launch_assistant, "_terminate_process", terminate)
    monkeypatch.setattr(launch_assistant, "_mark_interrupted_running_jobs", mark_interrupted)
    monkeypatch.setattr(launch_assistant.bootstrap, "main", startup)
    monkeypatch.setattr(sys, "argv", ["launch_assistant.py"])
    with registry.track():
        worker_thread = ThreadResult("cleanup-worker", lambda: job_worker.worker_loop_once(session_factory))
        try:
            assert child_entered.wait(WAIT_SECONDS)
            assert coordinator.active_operations == 1
            assert registry.has_active_children()
            assert coordinator.request_idle_stop(session_factory) == "service-busy"
            assert coordinator.stopping is False
            assert launch_assistant.main() == 2
            terminate.assert_not_called()
            mark_interrupted.assert_not_called()
            startup.assert_not_called()
            with pytest.raises(release.ReleaseLifecycleError, match="^residual-subprocess-requires-review$"):
                release.check_residual_jobs(environment.root / "assistant.sqlite3")
            with session_factory() as session:
                assert session.get(Job, execution["job_id"]).status == "running"
            assert children[0].arguments[-2:] == ["--execute", "--yes"]
            assert children[0].options["stdin"] == subprocess.DEVNULL
        finally:
            allow_exit.set()
            assert worker_thread.join() == execution["job_id"]
    assert not registry.has_active_children()
    assert list(registry.directory.glob("*.json")) == []
    children[0].kill.assert_not_called()
    children[0].terminate.assert_not_called()
    with session_factory() as session:
        completed_job = session.get(Job, execution["job_id"])
        assert completed_job.status == "succeeded", (completed_job.error_code, completed_job.error_summary)
    release.check_residual_jobs(environment.root / "assistant.sqlite3")
    assert coordinator.request_idle_stop(session_factory) == "accepted"


def test_uncertain_spawn_keeps_marker_and_blocks_stop(database, isolated_home, monkeypatch):
    class InterruptedProcess:
        def __init__(self, *arguments, **options):
            raise KeyboardInterrupt()

    monkeypatch.setattr(subprocess, "Popen", InterruptedProcess)
    registry = release.SubprocessRegistry(isolated_home / "runtime" / "children")
    _, _, session_factory = database
    session_factory.release_coordinator.subprocess_registry = registry
    with pytest.raises(KeyboardInterrupt):
        with registry.track():
            subprocess.Popen(["synthetic-child"])
    assert subprocess.Popen is InterruptedProcess
    assert registry.has_active_children()
    assert session_factory.release_coordinator.request_idle_stop(session_factory) == "service-busy"
    with pytest.raises(release.ReleaseLifecycleError, match="residual-subprocess-requires-review"):
        release.check_residual_jobs(isolated_home / "assistant.sqlite3")


def test_stopping_closes_subprocess_admission_before_any_spawn(database, isolated_home):
    _, _, session_factory = database
    coordinator = session_factory.release_coordinator
    registry = release.SubprocessRegistry(isolated_home / "runtime" / "children")
    registry.coordinator = coordinator
    coordinator.subprocess_registry = registry
    with registry.track():
        assert coordinator.request_idle_stop(session_factory) == "accepted"
        with pytest.raises(release.ServiceStopping):
            subprocess.Popen(["must-not-spawn"])
    assert not registry.directory.exists()


def mock_business_startup(monkeypatch):
    boundaries = {}
    for name in ("require_configuration", "load_user_environment", "check_residual_jobs"):
        boundaries[name] = Mock()
        monkeypatch.setattr(launcher, name, boundaries[name])
    boundaries["upgrade_database"] = Mock()
    monkeypatch.setattr(bootstrap, "upgrade_database", boundaries["upgrade_database"])
    boundaries["run_release_server"] = Mock(return_value=0)
    monkeypatch.setattr(launcher, "run_release_server", boundaries["run_release_server"])
    return boundaries


def assert_lock_held(lock_path):
    contender = lifecycle.InstanceLock(lock_path)
    try:
        assert contender.acquire() is False, f"Expected lock ownership at {lock_path.name}"
    finally:
        contender.release()


def assert_lock_released(lock_path):
    contender = lifecycle.InstanceLock(lock_path)
    try:
        assert contender.acquire() is True, f"Lock leaked at {lock_path.name}"
    finally:
        contender.release()


def test_source_bootstrap_owns_lock_before_configuration_and_migration(isolated_home, monkeypatch):
    call_order = []
    instance_path = isolated_home / "runtime" / "instance.lock"

    def guarded_step(name):
        def invoke(*arguments, **options):
            assert_lock_held(instance_path)
            call_order.append(name)
            return 0
        return invoke

    monkeypatch.setattr(bootstrap, "load_dotenv", guarded_step("environment"))
    monkeypatch.setattr(bootstrap, "configure_logging", guarded_step("logging"))
    monkeypatch.setattr(bootstrap, "upgrade_database", guarded_step("backup-and-migrate"))
    monkeypatch.setattr(lifecycle, "run_assistant", guarded_step("worker-and-server"))
    assert bootstrap.main() == 0
    assert call_order == ["environment", "logging", "backup-and-migrate", "worker-and-server"]
    assert_lock_released(instance_path)


def test_source_failed_lock_never_migrates_backs_up_or_starts_worker(isolated_home, monkeypatch):
    held_lock = lifecycle.InstanceLock(isolated_home / "runtime" / "instance.lock")
    assert held_lock.acquire()
    upgrade = Mock(side_effect=forbidden_boundary)
    worker = Mock(side_effect=forbidden_boundary)
    monkeypatch.setattr(bootstrap, "upgrade_database", upgrade)
    monkeypatch.setattr(bootstrap, "load_dotenv", forbidden_boundary)
    monkeypatch.setattr(bootstrap, "configure_logging", forbidden_boundary)
    monkeypatch.setattr(lifecycle, "start_worker", worker)
    monkeypatch.setattr(lifecycle, "_existing_instance_url", lambda path: None)
    try:
        assert bootstrap.main() == 2
        upgrade.assert_not_called()
        worker.assert_not_called()
        assert list((isolated_home / "backups").iterdir()) == []
        assert not (isolated_home / "assistant.sqlite3").exists()
    finally:
        held_lock.release()


def test_release_serve_owns_lock_before_config_backup_migration_and_server(isolated_home, monkeypatch):
    boundaries = mock_business_startup(monkeypatch)
    call_order = []
    for name, boundary in boundaries.items():
        def record(*arguments, step=name, **options):
            assert_lock_held(isolated_home / "runtime" / "instance.lock")
            call_order.append(step)
            return 0
        boundary.side_effect = record
    assert launcher.serve() == 0
    assert call_order == ["require_configuration", "load_user_environment", "check_residual_jobs",
                          "upgrade_database", "run_release_server"]
    boundaries["upgrade_database"].assert_called_once_with(sqlite_path=isolated_home / "assistant.sqlite3")
    assert_lock_released(isolated_home / "runtime" / "instance.lock")


def test_release_failed_instance_lock_precedes_all_business_startup(isolated_home, monkeypatch):
    boundaries = mock_business_startup(monkeypatch)
    held_lock = lifecycle.InstanceLock(isolated_home / "runtime" / "instance.lock")
    assert held_lock.acquire()
    try:
        with pytest.raises(release.ReleaseLifecycleError, match="^instance-busy-or-unknown$"):
            launcher.serve()
        for boundary in boundaries.values():
            boundary.assert_not_called()
    finally:
        held_lock.release()


def test_release_windows_stale_lock_is_not_reclaimed_by_pid(isolated_home, monkeypatch):
    lock_path = isolated_home / "runtime" / "instance.lock"
    lock_path.write_text("987654\n", encoding="ascii")
    monkeypatch.setattr(lifecycle.sys, "platform", "win32")
    monkeypatch.setattr(lifecycle, "_is_process_alive", forbidden_boundary)
    with pytest.raises(release.ReleaseLifecycleError, match="instance-busy-or-unknown"):
        launcher.acquire_lock("instance.lock")
    assert lock_path.read_text(encoding="ascii") == "987654\n"


def test_repeat_start_only_opens_browser_after_verified_health_without_migration(isolated_home, monkeypatch):
    release.write_private_json(isolated_home / "runtime" / "state.json", STATE)
    boundaries = mock_business_startup(monkeypatch)
    call_order = []

    def health(state):
        assert state == STATE
        assert_lock_held(isolated_home / "runtime" / "launch.lock")
        call_order.append("health")
        return {"version": "different-installed-version", "ok": True}

    monkeypatch.setattr(launcher, "verified_health", health)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda address: call_order.append(("browser", address)))
    for _ in range(2):
        assert launcher.start() == 0
    assert call_order == ["health", ("browser", "http://127.0.0.1:18765/"),
                          "health", ("browser", "http://127.0.0.1:18765/")]
    for boundary in boundaries.values():
        boundary.assert_not_called()
    assert release.read_private_json(isolated_home / "runtime" / "state.json") == STATE
    assert_lock_released(isolated_home / "runtime" / "launch.lock")


@pytest.mark.parametrize("mode", ("start", "serve", "operator"))
def test_stale_state_blocks_startup_without_migration_or_replacing_state(isolated_home, monkeypatch, mode):
    state_path = isolated_home / "runtime" / "state.json"
    release.write_private_json(state_path, STATE)
    before = state_path.read_bytes()
    boundaries = mock_business_startup(monkeypatch)
    health = Mock(return_value=None)
    monkeypatch.setattr(launcher, "verified_health", health)
    expected = "stop-console-before-operator" if mode == "operator" else "existing-instance-unverified"
    with pytest.raises(release.ReleaseLifecycleError, match=f"^{expected}$"):
        launcher.operator("prepare") if mode == "operator" else getattr(launcher, mode)()
    assert state_path.read_bytes() == before
    for boundary in boundaries.values():
        boundary.assert_not_called()
    assert_lock_released(isolated_home / "runtime" / "launch.lock")
    assert_lock_released(isolated_home / "runtime" / "instance.lock")


@pytest.mark.parametrize("mode", launcher.OPERATOR_MODES)
def test_operator_preserves_fixed_argv_and_excludes_concurrent_console_or_operator(isolated_home, monkeypatch, mode):
    boundaries = mock_business_startup(monkeypatch)
    operator_entered = threading.Event()
    allow_return = threading.Event()
    captured_arguments = []

    def launch_sample_main(arguments):
        captured_arguments.append(arguments)
        assert_lock_held(isolated_home / "runtime" / "launch.lock")
        assert_lock_held(isolated_home / "runtime" / "instance.lock")
        operator_entered.set()
        assert allow_return.wait(WAIT_SECONDS)
        return 17

    monkeypatch.setitem(sys.modules, "launch_sample", SimpleNamespace(main=launch_sample_main))
    operator_thread = ThreadResult("operator", lambda: launcher.operator(mode))
    try:
        assert operator_entered.wait(WAIT_SECONDS)
        with pytest.raises(release.ReleaseLifecycleError, match="^instance-busy-or-unknown$"):
            launcher.operator(mode)
        with pytest.raises(release.ReleaseLifecycleError, match="^instance-busy-or-unknown$"):
            launcher.start()
        with pytest.raises(release.ReleaseLifecycleError, match="^instance-busy-or-unknown$"):
            launcher.serve()
        boundaries["upgrade_database"].assert_not_called()
        boundaries["run_release_server"].assert_not_called()
    finally:
        allow_return.set()
        assert operator_thread.join() == 17
    assert captured_arguments == [[mode]]
    assert boundaries["require_configuration"].call_count == 1
    assert_lock_released(isolated_home / "runtime" / "launch.lock")
    assert_lock_released(isolated_home / "runtime" / "instance.lock")


@pytest.mark.parametrize("accepted", (False, True))
def test_stop_timeout_is_unknown_and_never_kills_or_removes_instance_state(isolated_home, monkeypatch, accepted):
    state_path = isolated_home / "runtime" / "state.json"
    release.write_private_json(state_path, STATE)
    original_state = state_path.read_bytes()
    monkeypatch.setattr(launcher, "verified_health", lambda state: {"ok": True})
    monkeypatch.setattr(launcher.uuid, "uuid4", lambda: SimpleNamespace(hex=REQUEST_ID))
    clock_values = iter((0.0, 0.0, 20.0))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock_values))
    monkeypatch.setattr(launcher.time, "sleep", lambda duration: None)
    kill = Mock(side_effect=forbidden_boundary)
    monkeypatch.setattr(launcher.os, "kill", kill)
    control_directory = isolated_home / "runtime" / "control"
    response_path = control_directory / f"response-{REQUEST_ID}.json"
    if accepted:
        release.write_private_json(response_path, {
            "instance_id": INSTANCE_ID, "request_id": REQUEST_ID, "result": "accepted",
        })
    with pytest.raises(release.ReleaseLifecycleError, match="^stop-timeout-unknown$"):
        launcher.stop()
    kill.assert_not_called()
    assert state_path.read_bytes() == original_state
    assert not (control_directory / f"request-{REQUEST_ID}.json").exists()
    assert not response_path.exists()


@pytest.mark.parametrize("health_changes", (
    {}, {"instance_id": "c" * 32}, {"pid": 54321}, {"app": "unrelated-service"}, {"ok": False},
))
def test_verified_health_binds_instance_and_pid_without_proxy_or_live_network(monkeypatch, health_changes):
    health = {"app": launcher.APP_NAME, "ok": True, "instance_id": INSTANCE_ID, "pid": STATE["pid"]}
    health.update(health_changes)
    response = Mock()
    response.read.return_value = json.dumps(health).encode("utf-8")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    build_opener = Mock(return_value=opener)
    monkeypatch.setattr(launcher.urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(launcher, "_is_process_alive", lambda process_id: True)
    assert launcher.verified_health(STATE) == (health if not health_changes else None)
    assert build_opener.call_args.args[0].proxies == {}
    redirect_handler = build_opener.call_args.args[1]
    assert redirect_handler.redirect_request(None, None, 302, "redirect", {}, "https://remote.invalid") is None
    opener.open.assert_called_once_with("http://127.0.0.1:18765/api/health", timeout=1)
    response.read.assert_called_once_with(16384)


@pytest.mark.parametrize("alive", (False, None))
def test_unverified_process_never_probes_network(alive, monkeypatch):
    monkeypatch.setattr(launcher, "_is_process_alive", lambda process_id: alive)
    assert launcher.verified_health(STATE) is None


@pytest.mark.parametrize("response_changes,expected", (
    ({"instance_id": "c" * 32}, "control-response-invalid"),
    ({"request_id": "c" * 32}, "control-response-invalid"),
    ({"result": "service-busy"}, "service-busy"),
    ({"result": "database-unknown"}, "database-unknown"),
))
def test_stop_rejects_wrong_response_identity_and_denied_stop(isolated_home, monkeypatch, response_changes, expected):
    state_path = isolated_home / "runtime" / "state.json"
    release.write_private_json(state_path, STATE)
    monkeypatch.setattr(launcher, "verified_health", lambda state: {"ok": True})
    monkeypatch.setattr(launcher.uuid, "uuid4", lambda: SimpleNamespace(hex=REQUEST_ID))
    response_path = isolated_home / "runtime" / "control" / f"response-{REQUEST_ID}.json"
    response = {"instance_id": INSTANCE_ID, "request_id": REQUEST_ID, "result": "accepted"}
    response.update(response_changes)
    release.write_private_json(response_path, response)
    with pytest.raises(release.ReleaseLifecycleError, match=f"^{expected}$"):
        launcher.stop()
    assert release.read_private_json(state_path) == STATE
    assert not response_path.exists()


def test_stop_reports_success_only_after_state_cleanup_and_instance_lock_release(isolated_home, monkeypatch):
    state_path = isolated_home / "runtime" / "state.json"
    release.write_private_json(state_path, STATE)
    held_lock = lifecycle.InstanceLock(isolated_home / "runtime" / "instance.lock")
    assert held_lock.acquire()
    monkeypatch.setattr(launcher, "verified_health", lambda state: {"ok": True})
    monkeypatch.setattr(launcher.uuid, "uuid4", lambda: SimpleNamespace(hex=REQUEST_ID))
    control_directory = isolated_home / "runtime" / "control"
    release.write_private_json(control_directory / f"response-{REQUEST_ID}.json", {
        "instance_id": INSTANCE_ID, "request_id": REQUEST_ID, "result": "accepted",
    })
    iterations = []

    def complete_shutdown(duration):
        iterations.append(duration)
        if len(iterations) == 1:
            state_path.unlink()
        else:
            # One iteration with state gone but ownership retained must not succeed.
            assert len(iterations) == 2
            held_lock.release()

    monkeypatch.setattr(launcher.time, "sleep", complete_shutdown)
    try:
        assert launcher.stop() == 0
        assert iterations == [0.1, 0.1]
    finally:
        held_lock.release()
    assert_lock_released(isolated_home / "runtime" / "instance.lock")


def test_stop_holds_launch_lock_until_shutdown_confirmation(isolated_home, monkeypatch):
    def confirm_shutdown():
        assert_lock_held(isolated_home / "runtime" / "launch.lock")
        with pytest.raises(release.ReleaseLifecycleError, match="instance-busy-or-unknown"):
            launcher.start()
        return 0

    monkeypatch.setattr(launcher, "request_service_stop", confirm_shutdown)
    assert launcher.stop() == 0
    assert_lock_released(isolated_home / "runtime" / "launch.lock")


def test_release_health_reports_frozen_startup_version_not_replaced_bundle(monkeypatch):
    application = app.create_app(port=18765)
    application.state.release_instance_id = INSTANCE_ID
    application.state.release_version = "original-version"
    application.state.release_target = "macos-arm64"
    monkeypatch.setattr(app, "application_version", forbidden_boundary)
    with TestClient(application, base_url="http://127.0.0.1:18765") as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["version"] == "original-version"
    assert response.json()["instance_id"] == INSTANCE_ID
    assert response.json()["target"] == "macos-arm64"


def test_health_handshake_rejects_version_drift_without_killing(monkeypatch):
    state = {**STATE, "version": "original-version", "target": "macos-arm64"}
    response = Mock()
    response.read.return_value = json.dumps({
        "app": launcher.APP_NAME, "ok": True, "instance_id": INSTANCE_ID,
        "pid": STATE["pid"], "version": "different-version", "target": "macos-arm64",
    }).encode("utf-8")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(launcher.urllib.request, "build_opener", lambda *arguments: opener)
    monkeypatch.setattr(launcher, "_is_process_alive", lambda process_id: True)
    assert launcher.verified_health(state) is None


@pytest.mark.parametrize("failure_kind,expected_code", [
    ("value", "release-operation-failed"),
    ("os", "release-operation-failed"),
    ("path", "release-resource-missing"),
    ("lifecycle", "complete-release-bundle-required"),
])
def test_launcher_reports_safe_exception_type_without_exception_values(
    isolated_home, monkeypatch, caplog, failure_kind, expected_code,
):
    secret_message = "synthetic-credential-must-not-appear"
    errors = {
        "value": ValueError(secret_message),
        "os": OSError(13, secret_message),
        "path": paths.ReleasePathError("release-resource-missing", secret_message),
        "lifecycle": release.ReleaseLifecycleError("complete-release-bundle-required"),
    }
    error = errors[failure_kind]

    def fail_resource_check():
        raise error

    monkeypatch.setattr(launcher, "check_release_resources", fail_resource_check)
    assert launcher.main(["diagnose"]) == 2
    assert expected_code in caplog.text
    assert "exception=" + type(error).__name__ in caplog.text
    assert secret_message not in caplog.text
    if failure_kind == "os":
        assert "errno=13" in caplog.text
