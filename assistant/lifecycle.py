"""Single-instance lifecycle and fixed-loopback server startup."""
from __future__ import annotations

import errno
import json
import logging
import os
import socket
import sys
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import uvicorn
from sqlalchemy.orm import sessionmaker

from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import Base
from assistant.jobs.locks import install_ziniao_busy_guard
from assistant.jobs.worker import start_worker
from assistant.paths import ensure_user_dirs
from assistant.settings import APP_NAME, BIND_HOST, preferred_port


logger = logging.getLogger(__name__)


def _read_windows_lock_pid(lock_path: Path) -> int | None:
    """Return a strictly parsed owner PID, or None when it is not trustworthy."""
    try:
        lock_contents = lock_path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None

    if lock_contents.endswith("\n"):
        lock_contents = lock_contents[:-1]
    if (
        not lock_contents
        or not lock_contents.isascii()
        or not lock_contents.isdigit()
    ):
        return None
    try:
        process_id = int(lock_contents)
    except ValueError:
        return None
    return process_id if process_id > 0 else None


def _is_process_alive(process_id: int) -> bool | None:
    """Return false only when the operating system confirms the PID is gone."""
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except ValueError:
        return None
    except OSError as error:
        if error.errno == errno.ESRCH:
            return False
        return None
    return True


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None
        self.windows_descriptor: int | None = None
        self.windows_file_identity: tuple[int, int] | None = None

    def _create_windows_lock(self) -> tuple[int, tuple[int, int]]:
        descriptor = os.open(
            self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        owner_pid = f"{os.getpid()}\n".encode("ascii")
        try:
            bytes_written = os.write(descriptor, owner_pid)
            if bytes_written != len(owner_pid):
                raise OSError("instance-lock-pid-write-incomplete")
            file_status = os.fstat(descriptor)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return descriptor, (file_status.st_dev, file_status.st_ino)

    def _try_reclaim_windows_lock(self) -> bool:
        owner_pid = _read_windows_lock_pid(self.path)
        if owner_pid is None or _is_process_alive(owner_pid) is not False:
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            try:
                (
                    self.windows_descriptor,
                    self.windows_file_identity,
                ) = self._create_windows_lock()
            except FileExistsError:
                if not self._try_reclaim_windows_lock():
                    return False
                try:
                    (
                        self.windows_descriptor,
                        self.windows_file_identity,
                    ) = self._create_windows_lock()
                except FileExistsError:
                    return False
                return True
            return True
        import fcntl

        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            return False
        return True

    def release(self) -> None:
        if self.windows_descriptor is not None:
            descriptor = self.windows_descriptor
            file_identity = self.windows_file_identity
            self.windows_descriptor = None
            self.windows_file_identity = None
            os.close(descriptor)
            if file_identity is None:
                return
            try:
                current_status = self.path.stat()
            except OSError:
                return
            current_identity = (current_status.st_dev, current_status.st_ino)
            if current_identity == file_identity:
                self.path.unlink(missing_ok=True)
        if self.handle is not None:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def choose_available_port(starting_port: int) -> int:
    for port in range(starting_port, min(starting_port + 100, 65536)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                # 旧实例刚退出时端口可能残留 TIME_WAIT 连接；
                # 与 uvicorn 一样允许复用，保证重启回到原端口。
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                candidate.bind((BIND_HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError("no-local-port-available")


def _existing_instance_url(state_path: Path) -> str | None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        port = int(state["port"])
        health_url = f"http://{BIND_HOST}:{port}/api/health"
        with urllib.request.urlopen(health_url, timeout=2) as response:
            health = json.load(response)
        if health.get("app") == APP_NAME:
            return f"http://{BIND_HOST}:{port}/"
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    return None


def run_assistant(*, uvicorn_runner: Callable = uvicorn.run) -> int:
    application_directory = ensure_user_dirs()
    runtime_directory = application_directory / "runtime"
    state_path = runtime_directory / "state.json"
    instance_lock = InstanceLock(runtime_directory / "instance.lock")
    if not instance_lock.acquire():
        existing_url = _existing_instance_url(state_path)
        if existing_url:
            webbrowser.open(existing_url)
            return 0
        return 2

    application = None
    database_engine = None
    worker_controller = None
    try:
        port = choose_available_port(preferred_port())
        application = create_app(port=port)
        database_engine = create_database_engine(
            application_directory / "assistant.sqlite3"
        )
        # Production startup has already run Alembic; this keeps direct test and
        # library invocation safe without replacing the migration path.
        Base.metadata.create_all(database_engine)
        session_factory = sessionmaker(bind=database_engine, expire_on_commit=False)
        application.state.database_engine = database_engine
        application.state.session_factory = session_factory
        install_ziniao_busy_guard(application, session_factory)
        worker_controller = start_worker(session_factory)
        state_path.write_text(
            json.dumps(
                {
                    "port": port,
                    "pid": os.getpid(),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        webbrowser.open(f"http://{BIND_HOST}:{port}/")
        uvicorn_runner(
            application,
            host=BIND_HOST,
            port=port,
            log_level="info",
            access_log=False,
        )
    finally:
        if worker_controller is not None:
            try:
                worker_controller.stop(timeout=5.0)
            except Exception:
                logger.exception("failed to stop assistant worker")
        if database_engine is not None:
            try:
                database_engine.dispose()
            except Exception:
                logger.exception("failed to dispose assistant database engine")
        try:
            state_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("failed to remove assistant state file")
        try:
            instance_lock.release()
        except Exception:
            logger.exception("failed to release assistant instance lock")
    return 0
