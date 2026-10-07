#!/usr/bin/env python3
"""Offline application acceptance with the release interpreter, never a venv.

The outer controller uses only the standard library. Every application process
is re-entered through this file with -I -B and an audit hook installed BEFORE
application imports. This is an accidental-I/O tripwire, not an OS security
sandbox for untrusted native code. No real configuration or business job is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import re
import runpy
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PureWindowsPath


logger = logging.getLogger(__name__)
SMOKE_SOURCE = Path(__file__).resolve()
SYNTHETIC_STORE_ID = "90000000000001"
APP_DIR_NAME = "ZnSampleAssistant"
SAFE_SYSTEM_READ_LABELS = {
    Path("/System/Library/CoreServices/SystemVersion.plist"): "macos-system-version",
    Path("/etc/mime.types"): "system-mime-database",
    Path("/etc/apache2/mime.types"): "system-mime-database",
    Path("/etc/apache/mime.types"): "system-mime-database",
    Path("/private/etc/mime.types"): "system-mime-database",
    Path("/private/etc/apache2/mime.types"): "system-mime-database",
    Path("/private/etc/apache/mime.types"): "system-mime-database",
}
SAFE_SYSTEM_READS = frozenset(SAFE_SYSTEM_READ_LABELS)
SMOKE_DIAGNOSTIC_LIMIT = 64 * 1024
SMOKE_DIAGNOSTIC_STAGES = frozenset({"controller", "launcher"})


class SmokeBlocked(RuntimeError):
    """A redacted, stable acceptance failure."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise SmokeBlocked(code)


def redact_smoke_output(value: bytes | str | None, sandbox: Path) -> str:
    """Retain bounded diagnostic output, not credentials or machine paths."""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
    secret_pattern = r"secret|token|password|credential|cookie|api[_-]?key|authorization"
    secret_values = {
        content for name, content in os.environ.items()
        if content and re.search(secret_pattern, name, re.I)
    }
    for secret in sorted(secret_values, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [redacted]", text)
    text = re.sub(
        rf'''(?ix)(\b[\w.-]*(?:{secret_pattern})[\w.-]*["']?\s*[:=]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;]+)''',
        lambda match: match.group(1) + "[redacted]", text,
    )
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", text)
    # Structured paths and traceback filenames can contain spaces or escaped
    # Windows separators. Remove the whole value, not just its first word.
    text = re.sub(r'''(?i)(File\s+)["'][^\n]*?["'](?=, line \d+)''', r'\1"[path]"', text)
    text = re.sub(
        r'''(?i)(["'](?:\w*_path|cwd|executable)["']\s*:\s*)("(?:\\.|[^"\\])*"|'[^'\n]*')''',
        lambda match: match.group(1) + '"[path]"', text,
    )
    for path in sorted({str(sandbox), str(sandbox.parent), str(SMOKE_SOURCE.parent), str(Path.home())}, key=len, reverse=True):
        for variant in {path, path.replace("\\", "/"), path.replace("\\", "\\\\")}:
            text = text.replace(variant, "[path]")
    text = re.sub(r'''["'](?:[A-Za-z]:[/\\]|/)[^\n]*?["']''', '"[path]"', text)
    text = re.sub(
        r'''(?i)(?<![\w:/\\])(?:[a-z]:[/\\]|/(?:usr|opt|home|Users|tmp|private|mingw64|ucrt64|cygdrive|[a-z])/)\S+''',
        "[path]", text,
    )
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(character for character in text if character in "\n\t" or ord(character) >= 32)
    if len(text) > SMOKE_DIAGNOSTIC_LIMIT:
        text = "[earlier output omitted]\n" + text[-SMOKE_DIAGNOSTIC_LIMIT:]
    return text


def record_smoke_process(
    sandbox: Path, *, stage: str, mode: str, returncode: int | None,
    expected_success: bool, stdout: bytes | str | None, stderr: bytes | str | None,
    failure_type: str | None = None,
) -> None:
    """Keep the last launcher result and controller failure within the sandbox."""
    require(stage in SMOKE_DIAGNOSTIC_STAGES, "smoke-diagnostic-stage-invalid")
    require(mode in {"inside", "start", "stop", "diagnose"}, "smoke-diagnostic-mode-invalid")
    try:
        directory = sandbox / "smoke-diagnostics"
        directory.mkdir(exist_ok=True)
        prefix = f"launcher-{mode}" if stage == "launcher" else stage
        metadata_path = directory / f"{prefix}-result.json"
        if metadata_path.is_file():
            previous = json.loads(metadata_path.read_text(encoding="utf-8"))
            previous_failed = previous["failure_type"] is not None or (
                (previous["returncode"] == 0) != previous["expected_success"]
            )
            if previous_failed:
                return  # Cleanup must not overwrite the first failure evidence.
        for stream_name, content in (("stdout", stdout), ("stderr", stderr)):
            (directory / f"{prefix}-{stream_name}.log").write_text(
                redact_smoke_output(content, sandbox), encoding="utf-8",
            )
        metadata = {
            "stage": stage, "mode": mode, "returncode": returncode,
            "expected_success": expected_success, "redacted": True,
            "failure_type": failure_type,
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2) + "\n", encoding="utf-8",
        )
    except (OSError, ValueError, KeyError):
        logger.warning("Smoke process diagnostics unavailable for %s", stage)


def export_smoke_failure_diagnostics(sandbox: Path, destination: Path | None) -> None:
    """Export fixed diagnostic files only, never a user directory or database."""
    if destination is None:
        return
    try:
        destination = destination.resolve()
        if destination.is_relative_to(sandbox.resolve()):
            raise SmokeBlocked("smoke-diagnostics-inside-sandbox")
        destination.mkdir(parents=True, exist_ok=True)
        source_directory = sandbox / "smoke-diagnostics"
        if source_directory.is_symlink():
            raise SmokeBlocked("smoke-diagnostics-source-symlink")
        for stage in ("controller", "launcher-diagnose", "launcher-start", "launcher-stop"):
            for suffix in ("stdout.log", "stderr.log", "result.json"):
                filename = f"{stage}-{suffix}"
                source = source_directory / filename
                if source.is_symlink() or not source.is_file():
                    continue
                # Sanitize again in the outer process, which knows parent
                # credential values excluded from the child environment.
                with source.open("r", encoding="utf-8") as stream:
                    content = stream.read(SMOKE_DIAGNOSTIC_LIMIT + 1024)
                with (destination / filename).open("x", encoding="utf-8") as stream:
                    stream.write(redact_smoke_output(content, sandbox))
    except (OSError, SmokeBlocked):
        logger.warning("Smoke failure diagnostic export unavailable")


def run_smoke_launcher(
    python_path: Path, launcher: Path, sandbox: Path, arguments: tuple[str, ...],
    *, timeout: float, expected_success: bool = True,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            [str(python_path), "-I", "-B", str(launcher), *arguments],
            cwd=sandbox, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        record_smoke_process(
            sandbox, stage="launcher", mode=arguments[0], returncode=None,
            expected_success=expected_success, stdout=getattr(error, "stdout", None),
            stderr=getattr(error, "stderr", None), failure_type=type(error).__name__,
        )
        raise
    record_smoke_process(
        sandbox, stage="launcher", mode=arguments[0], returncode=result.returncode,
        expected_success=expected_success, stdout=result.stdout, stderr=result.stderr,
    )
    violations_path = sandbox / "audit-violations.jsonl"
    if violations_path.exists():
        violations = [json.loads(line) for line in violations_path.read_text(encoding="utf-8").splitlines()]
        last_violation = violations[-1]
        suffix = last_violation.get("path_category") or ""
        resource_name = last_violation.get("resource_name") or ""
        if resource_name and all(character in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in resource_name):
            suffix += "-" + resource_name.lower()
        raise SmokeBlocked(last_violation["code"] + ("-" + suffix if suffix else ""))
    require((result.returncode == 0) == expected_success, "launcher-" + arguments[0] + "-unexpected-exit")
    return result


def run_smoke_controller(
    command: list[str], *, cwd: Path, environment: dict, process_options: dict,
    sandbox: Path, bundle_root: Path, timeout: float, diagnostics_dir: Path | None,
) -> subprocess.CompletedProcess:
    try:
        controller = subprocess.Popen(
            command, cwd=cwd, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, **process_options,
        )
    except OSError as error:
        record_smoke_process(
            sandbox, stage="controller", mode="inside", returncode=None,
            expected_success=True, stdout=None, stderr=None, failure_type=type(error).__name__,
        )
        export_smoke_failure_diagnostics(sandbox, diagnostics_dir)
        raise
    try:
        stdout, stderr = controller.communicate(timeout=timeout * 12)
    except subprocess.TimeoutExpired as error:
        record_smoke_process(
            sandbox, stage="controller", mode="inside", returncode=None,
            expected_success=True, stdout=error.stdout, stderr=error.stderr,
            failure_type=type(error).__name__,
        )
        try:
            stop_registered_smoke_server(sandbox, bundle_root)
            terminate_smoke_process_group(controller)
            controller.communicate(timeout=5)
        finally:
            export_smoke_failure_diagnostics(sandbox, diagnostics_dir)
        raise SmokeBlocked("smoke-outer-timeout-process-group-cleaned") from None
    if controller.returncode:
        record_smoke_process(
            sandbox, stage="controller", mode="inside", returncode=controller.returncode,
            expected_success=True, stdout=stdout, stderr=stderr,
        )
        export_smoke_failure_diagnostics(sandbox, diagnostics_dir)
    # Preserve the existing cleanup boundary even on early child failures.
    try:
        terminate_smoke_process_group(controller)
    except (OSError, SmokeBlocked, subprocess.TimeoutExpired) as error:
        record_smoke_process(
            sandbox, stage="controller", mode="inside", returncode=controller.returncode,
            expected_success=True, stdout=stdout, stderr=stderr, failure_type=type(error).__name__,
        )
        export_smoke_failure_diagnostics(sandbox, diagnostics_dir)
        raise
    return subprocess.CompletedProcess(
        command, controller.returncode, stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def contained_path(root: Path, value: object) -> Path:
    require(isinstance(value, str) and bool(value), "manifest-path-invalid")
    relative = Path(value)
    require(not relative.is_absolute() and not PureWindowsPath(value).drive
            and ".." not in relative.parts and "\\" not in value,
            "manifest-path-escape")
    candidate = (root / relative).resolve()
    require(candidate.is_relative_to(root), "manifest-path-escape")
    return candidate


def resolve_bundle_python(bundle_root: Path) -> tuple[Path, dict]:
    """Resolve only a manifest-pinned private interpreter; never shutil.which."""
    manifest = json.loads((bundle_root / "release-manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 1, "manifest-schema-invalid")
    require(manifest.get("scope") == "browser-application-bundle",
            "application-resources-not-integrated")
    python_path = contained_path(bundle_root, manifest["executables"]["python_executable"])
    require(python_path.is_file() and python_path.is_relative_to(bundle_root / "runtime/python"),
            "private-python-required")
    pinned_entries = [entry for entry in manifest["files"]
                      if contained_path(bundle_root, entry["path"]) == python_path and "sha256" in entry]
    require(bool(pinned_entries), "private-python-hash-required")
    digest = hashlib.sha256(python_path.read_bytes()).hexdigest()
    require(all(entry["sha256"] == digest for entry in pinned_entries), "private-python-hash-mismatch")
    require((bundle_root / "app/scripts/release_launcher.py").is_file(), "release-launcher-required")
    return python_path, manifest


def isolated_environment(sandbox: Path, port: int, parent: dict[str, str] | None = None) -> dict[str, str]:
    """Allowlist OS essentials; exclude secrets, proxy settings and dev runtimes."""
    source = os.environ if parent is None else parent
    # Windows os.environ uppercases keys; dict(os.environ) no longer provides
    # case-insensitive lookup. Preserve SystemRoot across every child hop so
    # Winsock can load its providers, without inheriting PATH or other values.
    essential_names = {name.upper(): name for name in ("SystemRoot", "WINDIR", "COMSPEC")}
    environment = {
        essential_names[name.upper()]: value
        for name, value in source.items() if name.upper() in essential_names
    }
    directories = {
        "HOME": "home", "USERPROFILE": "home", "APPDATA": "roaming",
        "LOCALAPPDATA": "local", "XDG_CONFIG_HOME": "xdg/config",
        "XDG_DATA_HOME": "xdg/data", "XDG_CACHE_HOME": "xdg/cache",
        "XDG_STATE_HOME": "xdg/state", "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp",
    }
    for name, relative in directories.items():
        directory = sandbox / relative
        directory.mkdir(parents=True, exist_ok=True)
        environment[name] = str(directory)
    environment.update(
        PATH="", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8", NO_PROXY="127.0.0.1,localhost",
        PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
        PYTHONTZPATH="", ZN_ASSISTANT_PORT=str(port), BROWSER="none",
    )
    return environment


def sandbox_user_data_dir(sandbox: Path, *, platform_name: str | None = None) -> Path:
    """Mirror assistant.paths.user_data_dir using only isolated HOME variables."""
    effective_platform = sys.platform if platform_name is None else platform_name
    if effective_platform == "darwin":
        return sandbox / "home/Library/Application Support" / APP_DIR_NAME
    if effective_platform == "win32":
        return sandbox / "local" / APP_DIR_NAME
    return sandbox / "home/.local/share" / APP_DIR_NAME


class OfflineAudit:
    """Block external filesystem, network, process and import access at source."""

    def __init__(self, bundle_root: Path, sandbox: Path, python_path: Path, source: Path = SMOKE_SOURCE):
        self.bundle_root = bundle_root.resolve()
        self.sandbox = sandbox.resolve()
        self.python_path = python_path.resolve()
        self.source = source.resolve()
        self.allowed_ports: set[int] = set()
        self.allowed_system_reads: set[str] = set()
        self.approved_ffmpeg_commands: set[tuple[str, ...]] = set()
        self.ffmpeg_path: Path | None = None
        self.violations: list[str] = []
        self.child_processes: list[subprocess.Popen] = []
        self.original_popen = subprocess.Popen
        self.pending_windows_launch = threading.local()
        fallback_socketpair = getattr(socket, "_fallback_socketpair", None)
        self.socketpair_code = getattr(fallback_socketpair, "__code__", None)

    def block(self, code: str, *, path_category: str = "", resource_name: str = "") -> None:
        self.violations.append(code)
        # Persist even if application code catches the exception, including in
        # detached children. Only the fixed sandbox path is ever written here.
        with (self.sandbox / "audit-violations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "code": code,
                                     "path_category": path_category,
                                     "resource_name": resource_name}) + "\n")
        raise SmokeBlocked(code)

    def classify_path(self, path: Path) -> str:
        if path.is_relative_to(self.bundle_root):
            return "bundle"
        if path.is_relative_to(self.sandbox):
            return "sandbox"
        if path.is_relative_to(self.python_path.parents[2]):
            return "private-runtime"
        if any(path.is_relative_to(root) for root in (
            Path("/System"), Path("/usr"), Path("/Library"), Path("/dev"),
            Path("/etc"), Path("/opt"), Path("/Applications"),
        )):
            return "system-resource"
        if path.is_relative_to(Path("/private/var")) or path.is_relative_to(Path("/private/tmp")):
            return "temporary-tree"
        if path.is_relative_to(Path("/private/etc")):
            return "system-resource"
        if path.is_relative_to(Path("/Users")):
            return "user-tree"
        if path.is_relative_to(Path("/home")):
            return "user-tree"
        if path.is_relative_to(Path("/var")):
            if not path.is_relative_to(Path("/var/run")):
                return "system-resource"
        return "external"

    def check_path(self, value: object, *, write: bool = False) -> None:
        if value is None or isinstance(value, int):
            return
        if not isinstance(value, (str, bytes, os.PathLike)):
            self.block("audit-path-invalid")
        candidate = Path(os.fsdecode(value)).resolve()
        if candidate.is_relative_to(self.sandbox):
            return
        if not write and (candidate.is_relative_to(self.bundle_root) or candidate == self.source):
            return
        if not write and candidate in SAFE_SYSTEM_READS:
            self.allowed_system_reads.add(SAFE_SYSTEM_READ_LABELS[candidate])
            return
        if candidate in {Path(os.devnull).resolve(), Path("/dev/urandom")}:
            return
        self.block("audit-external-write" if write else "audit-external-read",
                   path_category=self.classify_path(candidate),
                   resource_name=candidate.name if self.classify_path(candidate) == "system-resource" else "")

    def __call__(self, event: str, arguments: tuple) -> None:
        if event == "open":
            mode = arguments[1] or ""
            flags = arguments[2] or 0
            writes = any(character in mode for character in "wax+") or bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            self.check_path(arguments[0], write=writes)
        elif event in {"os.listdir", "os.scandir", "os.chdir"}:
            self.check_path(arguments[0])
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"}:
            self.check_path(arguments[0], write=True)
        elif event in {"os.rename", "os.link", "os.symlink"}:
            self.check_path(arguments[0], write=True)
            self.check_path(arguments[1], write=True)
        elif event == "import" and arguments[1]:
            self.check_path(arguments[1])
        elif event == "sqlite3.connect":
            database_name = os.fsdecode(arguments[0])
            if database_name != ":memory:":
                if database_name.startswith("file:"):
                    database_uri = urllib.parse.urlsplit(database_name)
                    if database_uri.netloc:
                        self.block("audit-sqlite-uri-authority")
                    database_name = urllib.parse.unquote(database_uri.path)
                    if sys.platform == "win32":
                        if PureWindowsPath(database_name).drive.startswith("\\\\"):
                            self.block("audit-sqlite-uri-authority")
                        # Path.as_uri() emits /D:/...; Windows Path.resolve()
                        # does not interpret that as the native D:/... path.
                        if re.match(r"^/[A-Za-z]:/", database_name):
                            database_name = database_name[1:]
                self.check_path(database_name, write=True)
        elif event == "ctypes.dlopen" and arguments[0]:
            self.check_path(arguments[0])
        elif event in {"socket.connect", "socket.bind", "socket.sendto"}:
            address = arguments[-1]
            if not isinstance(address, tuple) or len(address) < 2 or address[0] != "127.0.0.1":
                self.block("audit-non-loopback-network")
            if event != "socket.bind" and int(address[1]) not in self.allowed_ports:
                if event == "socket.connect" and self.is_internal_socketpair_connection(
                    arguments[0], address, sys._getframe(1),
                ):
                    return
                state_path = sandbox_user_data_dir(self.sandbox) / "runtime/state.json"
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    owned_port = state.get("port") == int(address[1]) and bool(state.get("instance_id"))
                except (OSError, ValueError):
                    owned_port = False
                if not owned_port:
                    self.block("audit-unregistered-loopback-network")
        elif event == "socket.getaddrinfo":
            if arguments[0] not in {"127.0.0.1", "localhost", None}:
                self.block("audit-remote-dns")
        elif event == "subprocess.Popen":
            executable, command = arguments[:2]
            if sys.platform == "win32" and isinstance(command, str):
                # CPython converts argv with list2cmdline before the Windows
                # audit event. Accept only the current wrapper's exact launch.
                expected = getattr(self.pending_windows_launch, "event", None)
                if expected is None or arguments != expected:
                    self.block("audit-external-interpreter")
                self.pending_windows_launch.event = None
                return
            if executable is None:
                if not isinstance(command, (list, tuple)) or not command:
                    self.block("audit-external-interpreter")
                executable = command[0]
            command_tuple = tuple(os.fsdecode(argument) for argument in command)
            private_python = Path(executable).resolve() == self.python_path
            approved_ffmpeg = (
                self.ffmpeg_path is not None
                and Path(executable).resolve() == self.ffmpeg_path
                and command_tuple in self.approved_ffmpeg_commands
            )
            if not private_python and not approved_ffmpeg:
                self.block("audit-external-interpreter")
        elif event in {"os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty"}:
            self.block("audit-unwrapped-process")

    def is_internal_socketpair_connection(self, client, address: tuple, caller) -> bool:
        """Allow only CPython's Windows socketpair self-pipe, not its port."""
        if sys.platform != "win32" or self.socketpair_code is None or caller.f_code is not self.socketpair_code:
            return False
        # The pinned stdlib fallback creates both sockets and connects directly
        # in this frame. A matching function name or a local port is not proof.
        if caller.f_locals.get("csock") is not client:
            return False
        listener = caller.f_locals.get("lsock")
        if not isinstance(client, socket.socket) or not isinstance(listener, socket.socket):
            return False
        if listener is client:
            return False
        if any(endpoint.family != socket.AF_INET or endpoint.type != socket.SOCK_STREAM
               for endpoint in (client, listener)):
            return False
        try:
            return (
                listener.getsockname() == address
                and address[0] == "127.0.0.1" and 0 < address[1] <= 65535
            )
        except OSError:
            return False

    def install(self) -> None:
        sys.addaudithook(self)
        subprocess.Popen = self.guarded_process_type(subprocess.Popen)

    def guarded_process_type(self, original_process_type):
        """Wrap Popen without breaking SubprocessRegistry's subclass contract."""
        audit = self

        class GuardedPopen(original_process_type):
            def __init__(self, arguments, *positional, **keywords):
                wrapped_arguments, child_script, child_role = audit.prepare_child(arguments, keywords)
                if sys.platform == "win32":
                    if getattr(audit.pending_windows_launch, "event", None) is not None:
                        audit.block("audit-nested-process-launch")
                    working_directory = keywords.get("cwd")
                    audit.pending_windows_launch.event = (
                        None, subprocess.list2cmdline(wrapped_arguments),
                        os.fsdecode(working_directory) if working_directory is not None else None,
                        dict(keywords["env"]),
                    )
                try:
                    super().__init__(wrapped_arguments, *positional, **keywords)
                finally:
                    if sys.platform == "win32":
                        audit.pending_windows_launch.event = None
                audit.child_processes.append(self)
                audit.record_child(self.pid, child_script, child_role)

        GuardedPopen.__name__ = "OfflineGuardedPopen"
        return GuardedPopen

    def prepare_child(self, arguments, keywords):
        """Validate and wrap children before the underlying Popen is entered."""
        if keywords.get("shell") or keywords.get("executable") or not isinstance(arguments, (list, tuple)):
            self.block("audit-shell-or-executable-override")
        command = [os.fspath(argument) for argument in arguments]
        if not command:
            self.block("audit-external-interpreter")
        executable_path = Path(command[0]).resolve()
        command_tuple = tuple(os.fsdecode(argument) for argument in command)
        if self.ffmpeg_path is not None and executable_path == self.ffmpeg_path:
            if command_tuple not in self.approved_ffmpeg_commands:
                self.block("audit-ffmpeg-command-not-approved")
            working_directory = Path(keywords.get("cwd") or os.getcwd()).resolve()
            if not working_directory.is_relative_to(self.sandbox):
                self.block("audit-ffmpeg-cwd-outside-sandbox")
            keywords["env"] = isolated_environment(
                self.sandbox, int(os.environ["ZN_ASSISTANT_PORT"]),
                dict(keywords.get("env") or os.environ),
            )
            return command, Path(command[0]), "ffmpeg"
        if executable_path != self.python_path:
            self.block("audit-external-interpreter")
        remaining = command[1:]
        while remaining:
            if remaining[0] in {"-I", "-B", "-u"}:
                remaining.pop(0)
            elif sys.platform == "win32" and remaining[:2] == ["-X", "utf8"]:
                remaining = remaining[2:]
            else:
                break
        if not remaining or remaining[0].startswith("-"):
            self.block("audit-unwrapped-python-command")
        script = Path(remaining[0]).resolve()
        is_guarded_launch = script == self.source and len(remaining) > 1 and remaining[1] == "--guarded-launch"
        if is_guarded_launch:
            wrapped = command
        else:
            self.check_path(script)
            if not script.is_relative_to(self.bundle_root / "app"):
                self.block("audit-child-script-outside-application")
            interpreter_flags = ["-I", "-B", "-X", "utf8"] if sys.platform == "win32" else ["-I", "-B"]
            wrapped = [str(self.python_path), *interpreter_flags, str(self.source), "--guarded-launch",
                       str(self.bundle_root), str(self.sandbox), str(script), *remaining[1:]]
        keywords["env"] = isolated_environment(
            self.sandbox, int(os.environ["ZN_ASSISTANT_PORT"]),
            dict(keywords.get("env") or os.environ),
        )
        target_script, target_arguments = script, remaining[1:]
        if script == self.source and len(remaining) > 1 and remaining[1] == "--guarded-launch":
            require(len(remaining) >= 6, "guarded-launch-arguments-invalid")
            target_script = Path(remaining[4]).resolve()
            target_arguments = remaining[5:]
        is_release_server = target_script.name == "release_launcher.py" and target_arguments == ["serve"]
        if is_release_server:
            # Keep the smoke service in the controller-owned process group. If
            # the harness times out, the outer controller can terminate only
            # this smoke-created group instead of orphaning a detached server.
            keywords["start_new_session"] = False
            creation_flags = int(keywords.get("creationflags", 0))
            creation_flags &= ~getattr(subprocess, "DETACHED_PROCESS", 0)
            creation_flags &= ~getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if "creationflags" in keywords:
                keywords["creationflags"] = creation_flags
        return wrapped, script, "release-server" if is_release_server else "smoke-helper"

    def record_child(self, child_pid: int, script: Path, role: str) -> None:
        children_directory = self.sandbox / "children"
        children_directory.mkdir(exist_ok=True)
        (children_directory / f"{child_pid}.json").write_text(
            json.dumps({"pid": child_pid, "parent_pid": os.getpid(),
                        "script": str(script), "role": role}),
            encoding="utf-8",
        )


def assert_private_runtime(python_path: Path, bundle_root: Path) -> None:
    require(Path(sys.executable).resolve() == python_path, "smoke-private-python-required")
    require(bool(sys.flags.isolated) and sys.dont_write_bytecode, "smoke-isolated-bytecode-flags-required")
    require(Path(sys.prefix).resolve().is_relative_to(bundle_root / "runtime/python"), "smoke-private-prefix-required")
    for search_path in sys.path:
        require(Path(search_path).resolve().is_relative_to(bundle_root / "runtime/python"), "smoke-development-import-path")
    for module in tuple(sys.modules.values()):
        origin = getattr(module, "__file__", None)
        if origin and not str(origin).startswith("<"):
            require(Path(origin).resolve().is_relative_to(bundle_root) or Path(origin).resolve() == SMOKE_SOURCE,
                    "smoke-development-import-origin")


def install_application_tripwires(bundle_root: Path, sandbox: Path, audit: OfflineAudit) -> None:
    """Never dispatch business jobs or open a real browser, even by accident."""
    import mimetypes
    import webbrowser

    # StaticFiles otherwise searches host mime.types databases on first asset
    # request. Bundle behavior should depend only on Python's built-in table.
    mimetypes.init(files=[])
    sys.path[:0] = [str(bundle_root / "app"), str(bundle_root / "app/scripts")]
    from assistant import paths
    expected_user_root = sandbox_user_data_dir(sandbox)
    require(paths.user_data_dir().resolve() == expected_user_root,
            "user-root-not-contained-in-sandbox-home")
    require(paths.configuration_dir().resolve() == expected_user_root / "config",
            "configuration-not-isolated")

    def record_healthy_browser_open(address, *arguments, **keywords):
        parsed_address = urllib.parse.urlsplit(address)
        state = json.loads((expected_user_root / "runtime/state.json").read_text(encoding="utf-8"))
        require(parsed_address.scheme == "http" and parsed_address.hostname == "127.0.0.1"
                and parsed_address.port == state["port"], "browser-open-url-not-owned")
        status, body, _headers = request_local(state["port"], "/api/health")
        health = json.loads(body)
        require(status == 200 and health.get("instance_id") == state["instance_id"]
                and health.get("pid") == state["pid"]
                and health.get("version") == state["version"], "browser-open-before-health")
        with (sandbox / "browser-opens.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": state["pid"], "instance_id": state["instance_id"],
                                     "health_status": status}) + "\n")
        return False

    webbrowser.open = record_healthy_browser_open
    webbrowser.open_new = webbrowser.open
    webbrowser.open_new_tab = webbrowser.open
    from assistant.jobs import registry
    original_handler = registry.get_handler

    def local_only_handler(job_type):
        if job_type != "report_export":
            audit.block("smoke-business-handler-dispatch-forbidden")
        return original_handler(job_type)

    registry.get_handler = local_only_handler
    from lib import zclaw
    # Page rendering asks for running stores. The offline fixture has no actual
    # browser; replace that read boundary, not the HTTP server or its routes.
    zclaw.list_running_stores = lambda: []

    def no_browser_business(*arguments, **keywords):
        audit.block("smoke-browser-business-forbidden")

    zclaw.zclaw_exec = no_browser_business
    zclaw.probe_store_page = no_browser_business


def guarded_launch(arguments: list[str]) -> int:
    bundle_root, sandbox, script = map(lambda value: Path(value).resolve(), arguments[:3])
    python_path, _manifest = resolve_bundle_python(bundle_root)
    assert_private_runtime(python_path, bundle_root)
    audit = OfflineAudit(bundle_root, sandbox, python_path)
    audit.install()
    install_application_tripwires(bundle_root, sandbox, audit)
    sys.argv = [str(script), *arguments[3:]]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exit_status:
        return int(exit_status.code or 0)
    return 0


def request_local(port: int, route: str, *, payload: dict | None = None) -> tuple[int, bytes, dict]:
    origin = f"http://127.0.0.1:{port}"
    headers = {"Origin": origin, "Content-Type": "application/json"}
    request = urllib.request.Request(origin + route, headers=headers,
                                     data=None if payload is None else json.dumps(payload).encode())
    # Disable environmental proxies and automatic redirects to other services.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *arguments, **keywords):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=3) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def wait_for(callback, timeout: float, code: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = callback()
            if result:
                return result
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.1)
    raise SmokeBlocked(code)


def choose_smoke_starting_port() -> int:
    """Choose an ephemeral loopback port with the lifecycle's 100-port margin."""
    for _attempt in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = int(reservation.getsockname()[1])
        if port < 65435:
            return port
    raise SmokeBlocked("smoke-port-range-unavailable")


def verify_bundle_manifest(bundle_root: Path) -> dict:
    """Verify all payload entries before accepting or relocating the bundle."""
    try:
        manifest = json.loads((bundle_root / "release-manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SmokeBlocked("release-manifest-invalid") from error
    require(manifest.get("scope") == "browser-application-bundle", "application-resources-not-integrated")
    require(manifest.get("target") in {"macos-arm64", "windows-x64"}, "release-target-invalid")
    entries = manifest.get("files")
    require(isinstance(entries, list) and bool(entries), "release-files-missing")
    expected_paths: set[str] = set()
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get("path"), str), "manifest-entry-invalid")
        relative_path = entry["path"]
        require(relative_path not in expected_paths, "manifest-entry-duplicate")
        expected_paths.add(relative_path)
        path = contained_path(bundle_root, relative_path)
        listed_path = bundle_root / relative_path
        if "symlink" in entry:
            require(listed_path.is_symlink() and os.readlink(listed_path) == entry["symlink"],
                    "manifest-symlink-mismatch")
            require(path.is_relative_to(bundle_root.resolve()), "manifest-symlink-escape")
        else:
            require(not listed_path.is_symlink() and listed_path.is_file(), "manifest-file-missing")
            require(isinstance(entry.get("sha256"), str)
                    and hashlib.sha256(listed_path.read_bytes()).hexdigest() == entry["sha256"],
                    "manifest-file-hash-mismatch")
            if "size" in entry:
                require(listed_path.stat().st_size == entry["size"], "manifest-file-size-mismatch")
    actual_paths = {
        path.relative_to(bundle_root).as_posix()
        for path in bundle_root.rglob("*")
        if path.name != "release-manifest.json" and (path.is_file() or path.is_symlink())
    }
    require(actual_paths == expected_paths, "manifest-payload-drift")
    for executable_field in ("python_executable", "node_executable", "ffmpeg_executable"):
        executable = contained_path(bundle_root, manifest.get("executables", {}).get(executable_field))
        require(executable.is_file(), "manifest-executable-missing")
        require(executable.is_relative_to(bundle_root / "runtime"), "manifest-executable-not-private")
    return manifest


def make_readonly_relocated_bundle(source_root: Path, sandbox: Path) -> Path:
    """Copy verified payload to an unrelated path and remove every write bit."""
    verify_bundle_manifest(source_root)
    destination_root = sandbox / "安装目录 with spaces"
    shutil.copytree(source_root, destination_root, symlinks=True)
    try:
        for path in destination_root.rglob("*"):
            if path.is_symlink():
                continue
            current_mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(current_mode & ~0o222)
        destination_root.chmod(stat.S_IMODE(destination_root.stat().st_mode) & ~0o222)
        verify_bundle_manifest(destination_root)
        for path in (destination_root, *destination_root.rglob("*")):
            if path.is_symlink():
                continue
            require(stat.S_IMODE(path.stat().st_mode) & 0o222 == 0, "relocated-bundle-not-readonly")
    except BaseException:
        restore_bundle_write_permissions(destination_root)
        raise
    return destination_root


def require_readonly_bundle(bundle_root: Path) -> None:
    """Reject smoke results unless every regular payload path lacks write bits."""
    for path in (bundle_root, *bundle_root.rglob("*")):
        if path.is_symlink():
            continue
        require(stat.S_IMODE(path.stat().st_mode) & 0o222 == 0,
                "relocated-bundle-not-readonly")


def restore_bundle_write_permissions(bundle_root: Path) -> None:
    """Make the temporary copy removable on platforms honoring directory modes."""
    if not bundle_root.exists():
        return
    paths = list(bundle_root.rglob("*"))
    for path in reversed(paths):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR | stat.S_IXUSR)
    bundle_root.chmod(stat.S_IMODE(bundle_root.stat().st_mode) | stat.S_IWUSR | stat.S_IXUSR)


def register_smoke_server_child(sandbox: Path, process_id: int) -> bool:
    marker_path = sandbox / "children" / f"{process_id}.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        script_path = Path(marker.get("script", "")).resolve()
    except (OSError, ValueError, TypeError):
        return False
    return (marker.get("pid") == process_id and marker.get("role") == "release-server"
            and script_path == (bundle_root / "app/scripts/release_launcher.py").resolve())


def stop_registered_smoke_server(sandbox: Path, bundle_root: Path, *, timeout: float = 8.0) -> bool:
    """Signal only a healthy release-server PID created and marked by smoke."""
    user_root = sandbox_user_data_dir(sandbox)
    state_path = user_root / "runtime/state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        process_id = state["pid"]
        port = state["port"]
        instance_id = state["instance_id"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if type(process_id) is not int or type(port) is not int or not isinstance(instance_id, str):
        return False
    if not register_smoke_server_child(sandbox, bundle_root, process_id):
        return False
    try:
        status, body, _headers = request_local(port, "/api/health")
        health = json.loads(body)
    except (OSError, ValueError):
        return False
    if (
        status != 200 or health.get("instance_id") != instance_id
        or health.get("pid") != process_id
        or health.get("version") != state.get("version")
        or health.get("target") != state.get("target")
    ):
        return False
    try:
        os.kill(process_id, signal.SIGTERM)
    except OSError:
        return not state_path.exists()
    try:
        wait_for(lambda: not state_path.exists(), timeout, "smoke-server-cleanup-timeout")
    except SmokeBlocked:
        return False
    return True


def terminate_smoke_process_group(process: subprocess.Popen, *, timeout: float = 8.0) -> None:
    """Stop the controller-owned process group after a smoke timeout/failure."""
    if os.name == "nt":
        try:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        except OSError:
            pass
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            return
    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=5)
    final_deadline = time.monotonic() + 2
    while time.monotonic() < final_deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise SmokeBlocked("smoke-process-group-cleanup-unverified")


def run_ffmpeg_smoke(audit: OfflineAudit, bundle_root: Path, sandbox: Path, manifest: dict) -> dict:
    """Exercise only the manifest-pinned local binary with generated media."""
    ffmpeg_path = contained_path(bundle_root, manifest["executables"]["ffmpeg_executable"])
    entry = next((item for item in manifest["files"] if item.get("path") ==
                  ffmpeg_path.relative_to(bundle_root).as_posix()), None)
    require(entry is not None and "sha256" in entry, "ffmpeg-manifest-entry-missing")
    require(hashlib.sha256(ffmpeg_path.read_bytes()).hexdigest() == entry["sha256"],
            "ffmpeg-manifest-hash-mismatch")
    audit.ffmpeg_path = ffmpeg_path.resolve()

    def run_command(arguments: list[str], *, binary_output: bool = False) -> bytes:
        command = [str(ffmpeg_path), *arguments]
        audit.approved_ffmpeg_commands.add(tuple(command))
        try:
            completed = subprocess.run(
                command, cwd=sandbox, stdin=subprocess.DEVNULL, capture_output=True,
                timeout=45, check=False, shell=False,
            )
        finally:
            audit.approved_ffmpeg_commands.discard(tuple(command))
        require(completed.returncode == 0, "ffmpeg-local-probe-failed")
        return completed.stdout if binary_output else completed.stdout + completed.stderr

    version_output = run_command(["-version"]).decode("utf-8", errors="replace")
    required_version = manifest.get("sources", {}).get("ffmpeg", {}).get("version")
    require(isinstance(required_version, str) and
            version_output.startswith(f"ffmpeg version {required_version} "),
            "ffmpeg-version-mismatch")
    license_output = run_command(["-L"]).decode("utf-8", errors="replace")
    normalized_license = " ".join(license_output.split())
    require("GNU Lesser General Public License" in normalized_license
            and not any(flag in version_output for flag in (
                "--enable-gpl", "--enable-nonfree", "--enable-version3")),
            "ffmpeg-license-policy-mismatch")

    media_directory = sandbox / "synthetic media"
    media_directory.mkdir()
    raw_path = media_directory / "input.rgb"
    video_path = media_directory / "output.mp4"
    raw_path.write_bytes(bytes((64, 128, 192)) * (16 * 16 * 4))
    run_command([
        "-nostdin", "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", "16x16", "-framerate", "1", "-i", str(raw_path),
        "-c:v", "mpeg4", "-y", str(video_path),
    ])
    decoded_frame = run_command([
        "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
        "-i", str(video_path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ], binary_output=True)
    require(len(decoded_frame) == 16 * 16 * 3,
            "ffmpeg-synthetic-decode-unverified")
    return {"version": required_version, "license_verified": True,
            "synthetic_decode_verified": True, "binary_relative_path":
            ffmpeg_path.relative_to(bundle_root).as_posix()}


def describe_external_cli(manifest: dict, bundle_root: Path) -> dict:
    """Report the declared technician prerequisite without running business CLI."""
    prerequisites = manifest.get("external_prerequisites")
    require(isinstance(prerequisites, list) and len(prerequisites) == 1,
            "external-cli-manifest-scope-invalid")
    prerequisite = prerequisites[0]
    require(isinstance(prerequisite, dict)
            and prerequisite.get("name") == "@ziniao-open/cli"
            and isinstance(prerequisite.get("version"), str)
            and prerequisite.get("bundled") is False,
            "external-cli-manifest-policy-invalid")
    require(not (bundle_root / "runtime/ziniao").exists(), "external-cli-was-bundled")
    return {"name": prerequisite["name"], "required_version": prerequisite["version"],
            "bundled": False, "version_probe": "not-run; no external CLI/config or business access"}


def require_native_target(manifest: dict) -> None:
    """Refuse cross-platform claims; acceptance must execute the target natively."""
    target = manifest.get("target")
    actual_system = platform.system()
    actual_machine = platform.machine().lower()
    target_matches = (
        target == "macos-arm64" and actual_system == "Darwin" and actual_machine in {"arm64", "aarch64"}
    ) or (
        target == "windows-x64" and actual_system == "Windows" and actual_machine in {"amd64", "x86_64"}
    )
    require(target_matches, "native-target-host-mismatch")


def verify_handler_adapters(bundle_root: Path, user_root: Path) -> list[str]:
    """Execute the actual fixed argv adapters with mocks, not business scripts."""
    from datetime import datetime
    from unittest.mock import patch
    from assistant.jobs.handlers import followup_send, operator, auto_approval
    from assistant import paths
    from lib import operator_launch
    records = []

    def capture_run(argv, **keywords):
        require(Path(argv[0]).resolve() == Path(sys.executable).resolve(), "handler-external-python")
        require(Path(argv[1]).resolve().is_relative_to(bundle_root / "app/scripts"), "handler-external-script")
        require(keywords.get("shell") is False, "handler-shell-enabled")
        require(Path(keywords["cwd"]).resolve() == bundle_root / "app", "handler-cwd-invalid")
        require("ZN_SAMPLE_USER_DATA_DIR" not in keywords["env"], "handler-user-root-override-present")
        expected_home = user_root.parent.parent / "home" if sys.platform == "win32" else user_root.parents[2]
        require(keywords["env"]["HOME"] == str(expected_home), "handler-home-not-isolated")
        if sys.platform == "win32":
            require(keywords["env"]["LOCALAPPDATA"] == str(user_root.parent),
                    "handler-localappdata-not-isolated")
        require(not any(name in keywords["env"] for name in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")),
                "handler-development-environment")
        records.append(Path(argv[1]).name)
        if "--out" in argv:
            output_prefix = Path(argv[argv.index("--out") + 1])
            require(output_prefix.resolve().is_relative_to(user_root / "exports"), "handler-output-outside-sandbox")
            output_prefix.with_suffix(".json").write_text(
                json.dumps([{"approve_status": "approved", "creator_name": "synthetic-only"}]), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    log_path = user_root / "logs/handler-smoke.log"
    with patch.object(subprocess, "run", side_effect=capture_run):
        for mode in ("prepare", "screen", "pipeline", "tracking"):
            return_code = operator_launch.run_operator_mode(
                mode, python=sys.executable,
                now=datetime(2026, 9, 30, 17, tzinfo=operator_launch.BEIJING),
                precheck_fn=lambda: {"store": {"storeId": SYNTHETIC_STORE_ID, "storeName": "Offline smoke"}},
                confirm_fn=lambda specification: True, force_fn=lambda: False,
                run_job_fn=lambda argv: operator._run_subprocess(argv, log_path=log_path),
                open_report_fn=lambda report_path: None, wait_fn=lambda: None,
                printer=lambda message: None, out_prefix=user_root / "exports" / ("handler-" + mode),
            )
            require(return_code == 0, "operator-adapter-" + mode + "-failed")
        require(followup_send._run_script(task_id=90001, job_id="synthetic-smoke", log_path=log_path) == 0,
                "followup-adapter-failed")
    auto_argv = auto_approval._fixed_argv({"store_id": SYNTHETIC_STORE_ID,
                                         "result_path": str(user_root / "exports/auto-smoke.json")}, "order-backfill")
    paths.assert_private_interpreter(auto_argv[0])
    require(Path(auto_argv[1]).resolve() == bundle_root / "app/scripts/auto_approval.py", "auto-adapter-script-invalid")
    records.append("auto_approval.py:argv-only")
    records.append("handler-adapters-no-business-mocks")
    return records


def run_smoke(bundle_root: Path, sandbox: Path, timeout: float) -> dict:
    python_path, manifest = resolve_bundle_python(bundle_root)
    verify_bundle_manifest(bundle_root)
    require_readonly_bundle(bundle_root)
    require_native_target(manifest)
    assert_private_runtime(python_path, bundle_root)
    audit = OfflineAudit(bundle_root, sandbox, python_path)
    requested_port = int(os.environ["ZN_ASSISTANT_PORT"])
    audit.install()
    install_application_tripwires(bundle_root, sandbox, audit)
    user_root = sandbox_user_data_dir(sandbox)
    user_root.mkdir(parents=True, exist_ok=True)
    for name in ("config", "exports", "logs", "backups", "runtime"):
        (user_root / name).mkdir(exist_ok=True)
    database = user_root / "assistant.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE smoke_sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO smoke_sentinel VALUES ('offline-only')")
    launcher = bundle_root / "app/scripts/release_launcher.py"
    state_path = user_root / "runtime/state.json"
    checks: list[str] = []
    cli_scope = describe_external_cli(manifest, bundle_root)
    ffmpeg_report = run_ffmpeg_smoke(audit, bundle_root, sandbox, manifest)
    checks.extend(["read-only-relocated-bundle", "native-ffmpeg-version-license-decode"])

    def launch(*arguments: str, expected_success: bool = True) -> subprocess.CompletedProcess:
        return run_smoke_launcher(
            python_path, launcher, sandbox, arguments,
            timeout=timeout, expected_success=expected_success,
        )

    def healthy_state():
        if not state_path.exists():
            return None
        state = json.loads(state_path.read_text(encoding="utf-8"))
        status, response, _headers = request_local(int(state["port"]), "/api/health")
        if status != 200:
            return None
        health = json.loads(response)
        require(health.get("ok") is True, "health-not-ok")
        require(bool(health.get("instance_id")) and health["instance_id"] == state.get("instance_id"),
                "health-instance-identity-missing-or-mismatched")
        require(health.get("pid") == state.get("pid"), "health-pid-missing-or-mismatched")
        require(health.get("version") == manifest["application_version"], "health-release-version-mismatch")
        require(health.get("target") == manifest["target"], "health-target-mismatch")
        return state

    try:
        database_before_configuration = hashlib.sha256(database.read_bytes()).hexdigest()
        diagnostic = launch("diagnose")
        require("configuration-required" in diagnostic.stdout, "missing-config-diagnostic-not-reported")
        rejected_start = launch("start", expected_success=False)
        require("configuration-required" in rejected_start.stdout, "missing-config-start-not-rejected")
        require(not state_path.exists() and not list((user_root / "backups").iterdir())
                and hashlib.sha256(database.read_bytes()).hexdigest() == database_before_configuration,
                "missing-config-touched-database-or-started-service")
        checks.append("configuration-required-no-worker-or-migration")
        (user_root / "config/config.toml").write_text(
            f'[stores]\ndefault_store_id = "{SYNTHETIC_STORE_ID}"\n'
            'default_store_name = "Offline smoke synthetic store"\n'
            f'prepare_store_id = "{SYNTHETIC_STORE_ID}"\n'
            'prepare_store_name = "Offline smoke synthetic store"\n', encoding="utf-8")
        # Keep a real listener occupied. A released port would only simulate,
        # not prove, the lifecycle's fallback-port behavior.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conflict:
            conflict.bind(("127.0.0.1", requested_port))
            conflict.listen(1)
            launch("start")
            state = wait_for(healthy_state, timeout, "server-health-timeout")
            require(int(state["port"]) != requested_port, "port-conflict-not-handled")
        checks.extend(["real-loopback-health", "occupied-port-fallback"])
        port = int(state["port"])
        audit.allowed_ports.add(port)
        initial_backups = set((user_root / "backups").glob("pre-upgrade-*.sqlite3"))
        launch("start")
        repeated_state = healthy_state()
        require(repeated_state["instance_id"] == state["instance_id"] and repeated_state["pid"] == state["pid"],
                "repeated-start-created-second-instance")
        require(set((user_root / "backups").glob("pre-upgrade-*.sqlite3")) == initial_backups,
                "repeated-start-reran-migration-backup")
        browser_opens = [json.loads(line) for line in (sandbox / "browser-opens.jsonl").read_text(encoding="utf-8").splitlines()]
        require(len(browser_opens) == 2 and all(event["health_status"] == 200
                and event["instance_id"] == state["instance_id"] for event in browser_opens),
                "browser-open-not-bound-to-healthy-instance")
        checks.append("health-before-browser-open")
        checks.append("repeated-start-same-instance")
        for route in ("/", "/prepare", "/auto-approval", "/reports"):
            status, body, _headers = request_local(port, route)
            require(status == 200 and bool(body), "page-resource-missing")
        static_root = bundle_root / "app/assistant/web/static"
        required_assets = ["console/assets/index.js", "console/assets/index.css",
                           "auto-approval/assets/index.js", "auto-approval/assets/index.css",
                           "fonts/Geist.woff2", "fonts/GeistMono.woff2",
                           "app.js", "console-shell.css", "geist-theme.css"]
        for relative in required_assets:
            asset = static_root / relative
            require(asset.is_file() and asset.stat().st_size > 0, "static-resource-missing")
            status, body, _headers = request_local(port, "/static/" + relative)
            require(status == 200 and body == asset.read_bytes(), "static-http-bytes-mismatch")
        from assistant.domain.sku_images import FOLLOWUP_IMAGE_PATH, FOLLOWUP_IMAGE_PRODUCT_ID, resolve_followup_attachment
        attachment = bundle_root / "app" / resolve_followup_attachment(product_id=FOLLOWUP_IMAGE_PRODUCT_ID)["path"]
        require(attachment.is_file() and bool(attachment.read_bytes()), "followup-attachment-missing")
        require((bundle_root / "app" / FOLLOWUP_IMAGE_PATH).resolve() == attachment.resolve(), "attachment-resolution-invalid")
        attachment_route = "/static/sop-images/" + urllib.parse.quote(attachment.name)
        status, body, _headers = request_local(port, attachment_route)
        require(status == 200 and body == attachment.read_bytes(), "attachment-http-bytes-mismatch")
        checks.extend(["rendered-pages-200", "static-builds-fonts-200", "followup-attachment-200"])
        with sqlite3.connect(database) as connection:
            require(bool(connection.execute("SELECT version_num FROM alembic_version").fetchone()), "migration-not-applied")
            require(connection.execute("SELECT value FROM smoke_sentinel").fetchone() == ("offline-only",), "migration-lost-data")
        backups = list((user_root / "backups").glob("pre-upgrade-*.sqlite3"))
        require(bool(backups), "pre-migration-backup-missing")
        with sqlite3.connect(backups[0]) as connection:
            require(connection.execute("PRAGMA integrity_check").fetchone() == ("ok",), "backup-corrupt")
            require(connection.execute("SELECT value FROM smoke_sentinel").fetchone() == ("offline-only",), "backup-lost-data")
        checks.extend(["actual-alembic-migration", "consistent-pre-migration-backup"])
        status, body, _headers = request_local(port, "/api/jobs/report-export", payload={"kind": "today"})
        require(status == 200, "export-job-creation-failed")
        job_id = json.loads(body)["job_id"]

        def export_completed():
            with sqlite3.connect(database) as connection:
                row = connection.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                require(row is not None and row[0] not in {"failed", "interrupted"}, "export-worker-failed")
                return row[0] == "succeeded"
        wait_for(export_completed, timeout, "local-export-worker-timeout")
        exports = list((user_root / "exports").glob("today_*.csv"))
        require(len(exports) == 1, "export-output-not-isolated")
        status, body, _headers = request_local(
            port, "/api/exports/download?filename=" + urllib.parse.quote(exports[0].name)
        )
        require(status == 200 and body == exports[0].read_bytes() and b"creator_name" in body,
                "export-download-mismatch")
        checks.append("local-worker-export-download")
        from sqlalchemy.orm import sessionmaker
        from assistant.database.engine import create_database_engine
        from assistant.database.models import Job, utc_now
        engine = create_database_engine(database)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        with sessions() as session:
            session.add(Job(id="synthetic-smoke-busy", job_type="operator_pipeline", store_id=SYNTHETIC_STORE_ID,
                            status="running", started_at=utc_now(), heartbeat_at=utc_now()))
            session.commit()
        try:
            launch("stop", expected_success=False)
            require(healthy_state()["instance_id"] == state["instance_id"], "busy-stop-terminated-server")
            for mode in ("prepare", "screen", "pipeline", "tracking"):
                launch("operator", mode, expected_success=False)
            with sessions() as session:
                require(session.query(Job).filter(Job.status == "pending").count() == 0, "busy-operator-created-business-job")
        finally:
            with sessions() as session:
                session.get(Job, "synthetic-smoke-busy").status = "succeeded"
                session.commit()
            engine.dispose()
        checks.extend(["busy-stop-rejected", "busy-operator-rejected-without-business"])
        checks.extend(verify_handler_adapters(bundle_root, user_root))
        launch("diagnose")
        checks.append("offline-diagnose")
        launch("stop")
        wait_for(lambda: not state_path.exists(), timeout, "idle-stop-state-not-removed")
        try:
            request_local(port, "/api/health")
        except OSError:
            pass
        else:
            raise SmokeBlocked("idle-stop-server-still-listening")
        checks.append("idle-stop-closed-listener")
        require(not (sandbox / "audit-violations.jsonl").exists(), "offline-audit-violation-recorded")
        require(not list((bundle_root / "app").rglob("__pycache__")), "release-bytecode-cache-present")
        verify_bundle_manifest(bundle_root)
        return {"ok": True, "version": manifest["application_version"], "checks": checks,
                "business_operations": 0, "audit": "all-application-processes-guarded",
                "relocated_bundle": True, "installation_root_readonly": True,
                "ffmpeg": ffmpeg_report, "external_cli": cli_scope,
                "allowed_system_reads": sorted(audit.allowed_system_reads),
                "native_platform": sys.platform, "host_system": platform.system(),
                "host_machine": platform.machine(), "release_target": manifest["target"],
                "manifest_sha256": hashlib.sha256(
                    (bundle_root / "release-manifest.json").read_bytes()).hexdigest()}
    finally:
        if state_path.exists():
            primary_error = sys.exception()
            try:
                launch("stop")
            except Exception as cleanup_error:
                server_stopped = stop_registered_smoke_server(sandbox, bundle_root)
                if primary_error is None:
                    raise SmokeBlocked("smoke-stop-command-failed-after-safe-fallback") from None
                if not server_stopped:
                    cleanup_code = (str(cleanup_error) if isinstance(cleanup_error, SmokeBlocked)
                                    else type(cleanup_error).__name__.lower())
                    primary_code = (str(primary_error) if isinstance(primary_error, SmokeBlocked)
                                    else "smoke-check-failed")
                    raise SmokeBlocked(f"{primary_code}-cleanup-{cleanup_code}") from None
        for child in audit.child_processes:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if arguments and arguments[0] == "--guarded-launch":
        return guarded_launch(arguments[1:])
    if arguments and arguments[0] == "--inside":
        sandbox = Path(arguments[2]).resolve()
        try:
            result = run_smoke(Path(arguments[1]).resolve(), sandbox, float(arguments[3]))
        except SmokeBlocked as error:
            violations_path = sandbox / "audit-violations.jsonl"
            if violations_path.exists():
                violations = [json.loads(line) for line in violations_path.read_text(encoding="utf-8").splitlines()]
                violation = violations[-1]
                suffix = violation.get("path_category") or ""
                resource_name = violation.get("resource_name") or ""
                if resource_name and all(character in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in resource_name):
                    suffix += "-" + resource_name.lower()
                if suffix and suffix not in str(error):
                    raise SmokeBlocked(str(error) + "-" + suffix) from None
            raise
        print(json.dumps(result, ensure_ascii=True))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--offline", action="store_true", required=True,
                        help="require the offline-only acceptance mode")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--diagnostics-dir", type=Path,
                        help="Optional package-external directory for redacted smoke failure reports")
    options = parser.parse_args(arguments)
    require(5 <= options.timeout <= 180, "smoke-timeout-invalid")
    original_environment = dict(os.environ)
    bundle_root = options.bundle.resolve()
    if options.diagnostics_dir is not None:
        require(not options.diagnostics_dir.resolve().is_relative_to(bundle_root),
                "smoke-diagnostics-inside-bundle")
    _source_python_path, _source_manifest = resolve_bundle_python(bundle_root)
    with tempfile.TemporaryDirectory(prefix="offline smoke ") as temporary:
        temporary_root = Path(temporary).resolve()
        sandbox = temporary_root / "沙箱 with spaces"
        sandbox.mkdir()
        relocated_bundle = make_readonly_relocated_bundle(bundle_root, sandbox)
        require_readonly_bundle(relocated_bundle)
        relocated_python, _relocated_manifest = resolve_bundle_python(relocated_bundle)
        cwd = sandbox / "随机 工作目录"
        cwd.mkdir()
        port = choose_smoke_starting_port()
        environment = isolated_environment(sandbox, port)
        controller_command = [str(relocated_python), "-I", "-B", str(SMOKE_SOURCE), "--inside",
                              str(relocated_bundle), str(sandbox), str(options.timeout)]
        controller_options = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        }
        result = run_smoke_controller(
            controller_command, cwd=cwd, environment=environment,
            process_options=controller_options, sandbox=sandbox, bundle_root=relocated_bundle,
            timeout=options.timeout, diagnostics_dir=options.diagnostics_dir,
        )
        if result.returncode:
            # Only report fixed codes; subprocess output can contain config data.
            for captured_output in (result.stderr, result.stdout):
                for line in reversed(captured_output.splitlines()):
                    marker = "Offline smoke blocked: "
                    if marker in line:
                        code = line.partition(marker)[2]
                        if code and all(character in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in code):
                            raise SmokeBlocked(code)
            exception_type = "unknown"
            if result.stderr.strip():
                final_line = result.stderr.strip().splitlines()[-1]
                candidate_type = final_line.partition(":")[0].strip().split(".")[-1]
                if candidate_type.isidentifier():
                    exception_type = candidate_type.lower()
            raise SmokeBlocked(
                f"bundle-application-smoke-failed-{exception_type}-exit-{result.returncode}"
            )
        report = json.loads(result.stdout.strip().splitlines()[-1])
        require(report.get("ok") is True, "smoke-report-invalid")
        require(options.offline and report.get("relocated_bundle") is True and
                report.get("installation_root_readonly") is True,
                "relocated-readonly-bundle-not-proven")
        require(dict(os.environ) == original_environment, "smoke-parent-environment-changed")
        report["parent_environment_unchanged"] = True
        print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        raise SystemExit(main())
    except (SmokeBlocked, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        if isinstance(error, SmokeBlocked):
            code = str(error)
        elif isinstance(error, subprocess.TimeoutExpired):
            code = "smoke-child-timeout"
        elif isinstance(error, OSError):
            code = f"smoke-local-os-error-{error.errno or 'unknown'}"
            if error.filename:
                code += "-" + Path(os.fsdecode(error.filename)).name[:64]
        else:
            code = f"smoke-local-{type(error).__name__.lower()}"
        logger.error("Offline smoke blocked: %s", code)
        raise SystemExit(2)
