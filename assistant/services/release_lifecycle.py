"""Release-only admission, identity-bound control and graceful server lifetime.

The coordinator is attached to a session factory, never a process-global mode.
Source-mode callers retain their existing behavior.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable

from fastapi.responses import JSONResponse
from sqlalchemy import select

from assistant.database.models import Job


class ReleaseLifecycleError(RuntimeError):
    """A safe, machine-readable failure without configuration contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ServiceStopping(ReleaseLifecycleError):
    def __init__(self) -> None:
        super().__init__("service-stopping")


class LifecycleCoordinator:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.stopping = False
        self.active_operations = 0
        self.subprocess_registry: SubprocessRegistry | None = None

    @contextmanager
    def admission(self):
        with self.lock:
            if self.stopping:
                raise ServiceStopping()
            yield

    @contextmanager
    def operation(self):
        with self.admission():
            self.active_operations += 1
        try:
            yield
        finally:
            with self.lock:
                self.active_operations -= 1

    def request_idle_stop(self, session_factory) -> str:
        # Enqueue and BEGIN IMMEDIATE claim hold this same lock until commit.
        with self.lock:
            if self.stopping:
                return "service-stopping"
            if self.active_operations:
                return "service-busy"
            try:
                if self.subprocess_registry is not None and self.subprocess_registry.has_active_children():
                    return "service-busy"
                with session_factory() as session:
                    busy_job = session.scalar(
                        select(Job.id).where(Job.status.in_(("pending", "running"))).limit(1)
                    )
            except Exception:
                return "database-unknown"
            if busy_job is not None:
                return "service-busy"
            self.stopping = True
            return "accepted"


def admission_guard(session_factory):
    coordinator = getattr(session_factory, "release_coordinator", None)
    return coordinator.admission() if coordinator is not None else nullcontext()


def check_residual_jobs(sqlite_path: Path) -> None:
    """Read before migration/reaping; never replay or rewrite abandoned work.

    All unfinished types are blocked conservatively, including read jobs which may
    contain local/Feishu writes and types unknown to this release.
    """
    subprocess_directory = sqlite_path.parent / "runtime" / "children"
    if subprocess_directory.is_symlink():
        raise ReleaseLifecycleError("runtime-path-unsafe")
    if subprocess_directory.exists() and any(subprocess_directory.iterdir()):
        raise ReleaseLifecycleError("residual-subprocess-requires-review")
    if not sqlite_path.exists():
        return
    try:
        with sqlite3.connect(sqlite_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "jobs" not in tables:
                return
            if connection.execute("SELECT 1 FROM jobs WHERE status IN ('pending','running') LIMIT 1").fetchone():
                raise ReleaseLifecycleError("residual-jobs-require-review")
    except sqlite3.Error as error:
        raise ReleaseLifecycleError("database-unknown") from error


def write_private_json(path: Path, payload: dict) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise ReleaseLifecycleError("runtime-path-unsafe")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_private_json(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 16384:
        raise ReleaseLifecycleError("runtime-state-invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReleaseLifecycleError("runtime-state-invalid")
    return payload


class SubprocessRegistry:
    """Track even children left alive by a handler exception.

    The release process owns subprocess.Popen for its whole lifetime. Existing
    fixed handlers use it directly or through subprocess.run; neither argv nor
    secrets are stored. A pre-spawn marker survives crashes and fails closed.
    """
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.lock = threading.RLock()
        self.children: dict[str, subprocess.Popen] = {}
        self.coordinator: LifecycleCoordinator | None = None

    def has_active_children(self) -> bool:
        with self.lock:
            for child_id, process in tuple(self.children.items()):
                if process.poll() is not None:
                    (self.directory / f"{child_id}.json").unlink(missing_ok=True)
                    del self.children[child_id]
            unknown_markers = self.directory.exists() and any(self.directory.iterdir())
            return bool(self.children) or unknown_markers

    @contextmanager
    def track(self):
        registry = self
        original_process_type = subprocess.Popen

        class TrackedProcess(original_process_type):
            def __init__(self, *arguments, **options):
                child_id = uuid.uuid4().hex
                marker_path = registry.directory / f"{child_id}.json"
                # Serialize a spawn with the stop decision and registry polling.
                admission = registry.coordinator.admission() if registry.coordinator is not None else nullcontext()
                with admission, registry.lock:
                    write_private_json(marker_path, {"state": "spawning", "owner_pid": os.getpid()})
                    try:
                        super().__init__(*arguments, **options)
                    except BaseException as error:
                        process_id = getattr(self, "pid", None)
                        if isinstance(error, OSError) and process_id is None:
                            # Popen's OS-level spawn failure did not create a child.
                            marker_path.unlink(missing_ok=True)
                        elif process_id is not None:
                            registry.children[child_id] = self
                        # Interrupted/uncertain spawn retains its pre-spawn marker.
                        raise
                    registry.children[child_id] = self
                    write_private_json(marker_path, {"state": "started", "pid": self.pid, "owner_pid": os.getpid()})

        subprocess.Popen = TrackedProcess
        try:
            yield self
        finally:
            # Never release the instance/maintenance lock while a child writes.
            with self.lock:
                children = tuple(self.children.values())
            for process in children:
                process.wait()
            self.has_active_children()
            subprocess.Popen = original_process_type


def install_release_guard(application, coordinator: LifecycleCoordinator) -> None:
    from assistant.security.csrf import is_local_host, is_valid_local_origin

    @application.exception_handler(ServiceStopping)
    async def reject_admission(request, error):
        return JSONResponse({"detail": "service-stopping"}, status_code=503)

    @application.middleware("http")
    async def track_local_operations(request, call_next):
        valid_host = is_local_host(request.headers.get("host", ""))
        valid_origin = request.method in {"GET", "HEAD", "OPTIONS"} or is_valid_local_origin(
            request.headers.get("origin", ""), application.state.port,
        )
        if not valid_host or not valid_origin:
            # Preserve the app's existing 400/403 security response even while
            # stopping, without admitting an operation or touching the database.
            return await call_next(request)
        if request.url.path == "/api/health":
            return await call_next(request)
        try:
            # Includes synchronous conversation previews and diagnostic probes,
            # not just durable jobs. Their child processes finish before return.
            with coordinator.operation():
                return await call_next(request)
        except ServiceStopping:
            return JSONResponse({"detail": "service-stopping"}, status_code=503)


class ControlMonitor:
    def __init__(self, directory: Path, instance_id: str, coordinator: LifecycleCoordinator,
                 session_factory, request_shutdown: Callable[[], None]) -> None:
        self.directory = directory
        self.instance_id = instance_id
        self.coordinator = coordinator
        self.session_factory = session_factory
        self.request_shutdown = request_shutdown
        self.seen_requests: set[str] = set()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="release-control", daemon=True)
        if directory.is_symlink():
            raise ReleaseLifecycleError("runtime-path-unsafe")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            directory.chmod(0o700)

    def process_request(self, path: Path) -> str:
        request_id = path.stem.removeprefix("request-")
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            return "invalid-request"
        response_path = self.directory / f"response-{request_id}.json"
        try:
            payload = read_private_json(path)
            valid = (
                set(payload) == {"action", "instance_id", "request_id"}
                and payload["action"] == "stop"
                and payload["instance_id"] == self.instance_id
                and payload["request_id"] == request_id
                and request_id not in self.seen_requests
            )
            self.seen_requests.add(request_id)
            result = self.coordinator.request_idle_stop(self.session_factory) if valid else "invalid-request"
        except (OSError, ValueError, ReleaseLifecycleError):
            result = "invalid-request"
        write_private_json(response_path, {"instance_id": self.instance_id, "request_id": request_id, "result": result})
        path.unlink(missing_ok=True)
        if result == "accepted":
            self.request_shutdown()
        return result

    def run(self) -> None:
        while not self.stop_event.wait(0.1):
            for path in sorted(self.directory.glob("request-*.json")):
                try:
                    self.process_request(path)
                except OSError:
                    # No reply is an unknown outcome, never permission to kill.
                    continue

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join()


def run_release_server(application_directory: Path, instance_lock) -> int:
    if instance_lock.handle is None and instance_lock.windows_descriptor is None:
        raise ReleaseLifecycleError("release-instance-lock-required")
    registry = SubprocessRegistry(application_directory / "runtime" / "children")
    with registry.track():
        return _run_tracked_release_server(application_directory, registry)


def _run_tracked_release_server(application_directory: Path, registry: SubprocessRegistry) -> int:
    """Caller owns the instance lock through migration and this entire call."""
    import uvicorn
    from sqlalchemy.orm import sessionmaker
    from assistant.app import application_version, create_app
    from assistant.database.engine import create_database_engine
    from assistant.jobs.locks import install_ziniao_busy_guard
    from assistant.jobs.worker import start_worker
    from assistant.lifecycle import choose_available_port
    from assistant.paths import _release_manifest
    from assistant.settings import BIND_HOST, preferred_port

    coordinator = LifecycleCoordinator()
    coordinator.subprocess_registry = registry
    registry.coordinator = coordinator
    database_engine = create_database_engine(application_directory / "assistant.sqlite3")
    worker_controller = None
    monitor = None
    state_path = application_directory / "runtime" / "state.json"
    try:
        session_factory = sessionmaker(bind=database_engine, expire_on_commit=False)
        session_factory.release_coordinator = coordinator
        port = choose_available_port(preferred_port())
        application = create_app(port=port)
        application.state.database_engine = database_engine
        application.state.session_factory = session_factory
        instance_id = uuid.uuid4().hex
        application.state.release_instance_id = instance_id
        application.state.release_version = application_version()
        release = _release_manifest()
        application.state.release_target = release[1]["target"] if release else "test"
        install_ziniao_busy_guard(application, session_factory)
        install_release_guard(application, coordinator)
        server = uvicorn.Server(uvicorn.Config(application, host=BIND_HOST, port=port, access_log=False))
        # Recheck before the source worker's stale reaper can touch any row.
        check_residual_jobs(application_directory / "assistant.sqlite3")
        worker_controller = start_worker(session_factory)
        monitor = ControlMonitor(application_directory / "runtime" / "control", instance_id,
                                 coordinator, session_factory, lambda: setattr(server, "should_exit", True))
        write_private_json(state_path, {"port": port, "pid": os.getpid(),
                                       "instance_id": instance_id, "version": application.state.release_version,
                                       "target": application.state.release_target})
        monitor.thread.start()
        server.run()
    finally:
        with coordinator.lock:
            coordinator.stopping = True
        if monitor is not None and monitor.thread.ident is not None:
            monitor.stop()
        if worker_controller is not None:
            # No cancellation or timeout-as-success. Retain ownership until a
            # running handler (and its synchronous children) actually returns.
            worker_controller.stop_event.set()
            worker_controller.thread.join()
        database_engine.dispose()
        state_path.unlink(missing_ok=True)
    return 0
