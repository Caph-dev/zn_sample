"""Single-instance lifecycle and fixed-loopback server startup."""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import uvicorn

from assistant.app import create_app
from assistant.paths import ensure_user_dirs
from assistant.settings import APP_NAME, BIND_HOST, preferred_port


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None
        self.windows_descriptor: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            try:
                self.windows_descriptor = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError:
                return False
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
            os.close(self.windows_descriptor)
            self.windows_descriptor = None
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

    port = choose_available_port(preferred_port())
    application = create_app(runtime_directory=runtime_directory, port=port)
    token = application.state.session_manager.issue_bootstrap_token()
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
    webbrowser.open(f"http://{BIND_HOST}:{port}/bootstrap?token={token}")
    try:
        # The bootstrap credential travels in the first URL query, so access
        # logging stays disabled to keep that one-time value out of logs.
        uvicorn_runner(
            application,
            host=BIND_HOST,
            port=port,
            log_level="info",
            access_log=False,
        )
    finally:
        application.state.session_manager.cleanup()
        state_path.unlink(missing_ok=True)
        instance_lock.release()
    return 0
