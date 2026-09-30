"""Offline, same-machine maintenance; deliberately standard-library only.

No worker, migrations, business handlers or remote clients are imported here.
The apply implementation is exercised with synthetic data; public apply remains
closed until plan 009 establishes lock-before-migration startup.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from assistant.jobs.registry import WRITE_JOB_TYPES
from assistant.paths import ReleasePathError, user_data_dir


LOCK_FIRST_STARTUP_READY = False  # 009 must establish the startup contract first.
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * MAX_FILE_BYTES
MAX_FILES = 100_000
MAX_CONFIG_BYTES = 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
DATABASE_READ_TIMEOUT_SECONDS = 15
CONFIG_FIELDS = {
    "stores": {"default_store_id", "default_store_name", "prepare_store_id", "prepare_store_name"},
    "feishu": {"app_id", "app_secret", "hero_url", "url", "sheet", "sheet_title", "bitable"},
    "feishu.bitable": {
        "app_id", "app_secret", "app_token", "base_app_token", "table_id",
        "base_table_id", "view_id", "base_view_id",
    },
    "content_review": {
        "enabled", "cache_dir", "external_env_path", "tikhub_api_key", "ark_api_key",
        "ark_model", "ffmpeg_path", "max_visual_videos", "max_pages", "max_videos",
        "max_frames", "max_media_bytes", "run_timeout_seconds",
    },
}
ENV_FIELDS = {
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL_ID", "TIKHUB_API_KEY", "ARK_API_KEY",
    "ARK_MODEL", "FFMPEG_PATH", "ZN_SAMPLE_STORE_ID", "ZN_SAMPLE_STORE_NAME",
    "ZN_SAMPLE_PREPARE_STORE_ID", "ZN_SAMPLE_PREPARE_STORE_NAME",
    "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_HERO_URL", "FEISHU_HERO_SHEET",
    "FEISHU_BITABLE_APP_ID", "FEISHU_BITABLE_APP_SECRET", "FEISHU_BITABLE_APP_TOKEN",
    "FEISHU_BITABLE_TABLE_ID", "FEISHU_BITABLE_VIEW_ID",
} | {"CONTENT_REVIEW_" + name.upper() for name in CONFIG_FIELDS["content_review"]
     if name not in {"tikhub_api_key", "ark_api_key", "ark_model", "ffmpeg_path"}}
MACHINE_ENV_FIELDS = {"CONTENT_REVIEW_EXTERNAL_ENV_PATH", "FFMPEG_PATH", "CONTENT_REVIEW_CACHE_DIR"}
ENTRYPOINT_PATTERN = re.compile(
    r"(?:^|[/\\\s])(?:launch_assistant|open_sample_store|screen_sample_requests|"
    r"send_sample_intro|sync_shipped_tracking|send_followup_message|auto_approval|"
    r"operator_launch)\.py(?:[\s\"']|$)|assistant\.bootstrap|"
    r"-m\s+scripts\.(?:open_sample_store|screen_sample_requests|send_sample_intro|"
    r"sync_shipped_tracking|send_followup_message|auto_approval)(?:\s|$)"
)


class UnsafeImport(RuntimeError):
    """Only a fixed diagnostic code may cross the reporting boundary."""


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    category: str
    size: int
    sha256: str
    identity: tuple[int, int, int, int, int]


@dataclass
class ImportReport:
    source: str
    target: str
    result: str = "planned"
    entries: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if "local-io-failure" in self.issues:
            return 1
        if self.result in {"blocked", "conflict"}:
            return 2
        return 0

    def as_dict(self) -> dict:
        return {
            "source": self.source, "target": self.target, "result": self.result,
            "counts": {category: sum(entry["category"] == category for entry in self.entries)
                       for category in sorted({entry["category"] for entry in self.entries})},
            "entries": self.entries, "issues": self.issues,
            "missing_fields": self.missing_fields,
        }


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    status = path.stat()
    return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns


def _safe_path(path: Path, root: Path) -> Path:
    """Reject links (including internal ones), devices and escaped ancestors."""
    if not path.is_relative_to(root):
        raise UnsafeImport("path-escape")
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise UnsafeImport("symlink-not-allowed")
    if not path.resolve().is_relative_to(root.resolve()):
        raise UnsafeImport("path-escape")
    return path


def _snapshot(path: Path, root: Path, category: str) -> SourceFile:
    _safe_path(path, root)
    before = _identity(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise UnsafeImport("non-regular-file")
    limit = MAX_CONFIG_BYTES if category == "configuration" else MAX_FILE_BYTES
    if before[2] > limit:
        raise UnsafeImport("file-size-limit")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if before != _identity(path):
        raise UnsafeImport("source-changing")
    return SourceFile(path, path.relative_to(root).as_posix(), category, before[2], digest, before)


def _check_stable(files: list[SourceFile], source_root: Path) -> None:
    for source in files:
        try:
            current = _snapshot(source.path, source_root, source.category)
        except FileNotFoundError:
            raise UnsafeImport("source-changing") from None
        if current.identity != source.identity or current.sha256 != source.sha256:
            raise UnsafeImport("source-changing")


def _inventory(source_root: Path) -> list[SourceFile]:
    files = []
    for relative_path in ("config.toml", "config/config.toml", ".env"):
        candidate = _safe_path(source_root / relative_path, source_root)
        if candidate.exists():
            files.append(_snapshot(candidate, source_root, "configuration"))
    export_root = _safe_path(source_root / "exports", source_root)
    if export_root.exists():
        if not export_root.is_dir():
            raise UnsafeImport("exports-not-directory")
        def reject_unreadable_directory(error: OSError) -> None:
            raise error

        for directory, subdirectories, filenames in os.walk(export_root, followlinks=False, onerror=reject_unreadable_directory):
            for name in subdirectories:
                _safe_path(Path(directory) / name, source_root)
            for name in sorted(filenames):
                candidate = Path(directory) / name
                category = "content-cache" if candidate.is_relative_to(export_root / "content_review_cache") else "exports"
                files.append(_snapshot(candidate, source_root, category))
                if len(files) > MAX_FILES:
                    raise UnsafeImport("file-count-limit")
    if sum(source.size for source in files) > MAX_TOTAL_BYTES:
        raise UnsafeImport("total-size-limit")
    return sorted(files, key=lambda source: source.relative_path)


def _validate_configuration(files: list[SourceFile], report: ImportReport) -> dict[Path, SourceFile]:
    configuration_files = [source for source in files if source.category == "configuration"]
    raw_config: dict = {}
    has_project_configuration = False
    environment: dict[str, str] = {}
    selected_files = {}
    for source in configuration_files:
        content = source.path.read_text(encoding="utf-8")
        if source.path.name == "config.toml":
            parsed = tomllib.loads(content)
            for section, values in parsed.items():
                if section not in {"stores", "feishu", "content_review"} or not isinstance(values, dict):
                    report.issues.append("unknown-config-field:" + section)
                    continue
                for name, value in values.items():
                    if name not in CONFIG_FIELDS[section]:
                        report.issues.append("unknown-config-field:" + section + "." + name)
                    elif section == "feishu" and name == "bitable":
                        if not isinstance(value, dict):
                            report.issues.append("invalid-config-section:feishu.bitable")
                        else:
                            for nested_name in value:
                                if nested_name not in CONFIG_FIELDS["feishu.bitable"]:
                                    report.issues.append("unknown-config-field:feishu.bitable." + nested_name)
                                elif not isinstance(value[nested_name], str):
                                    report.issues.append("invalid-config-field:feishu.bitable." + nested_name)
                    elif section != "content_review" and not isinstance(value, str):
                        report.issues.append("invalid-config-field:" + section + "." + name)
                    elif section == "content_review":
                        if name == "enabled":
                            valid_value = type(value) is bool
                        elif name.startswith("max_") or name == "run_timeout_seconds":
                            valid_value = type(value) is int and value > 0
                        else:
                            valid_value = isinstance(value, str)
                        if not valid_value:
                            report.issues.append("invalid-config-field:content_review." + name)
            review = parsed.get("content_review", {})
            if isinstance(review, dict):
                for name in ("external_env_path", "ffmpeg_path", "cache_dir"):
                    if review.get(name) and not (name == "cache_dir" and review[name] == "exports/content_review_cache"):
                        report.issues.append("machine-path-review:content_review." + name)
            if has_project_configuration and parsed != raw_config:
                report.issues.append("multiple-config-files-conflict")
            raw_config = parsed
            has_project_configuration = True
            selected_files[Path("config/config.toml")] = source
        else:
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                name, separator, value = stripped.partition("=")
                name = name.strip()
                if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                    report.issues.append("invalid-dotenv-line")
                    continue
                if name not in ENV_FIELDS:
                    report.issues.append("unknown-env-field:" + name)
                if name in environment:
                    report.issues.append("duplicate-env-field:" + name)
                environment[name] = value.strip().strip("\"'")
                if name in MACHINE_ENV_FIELDS and environment[name] and not (
                    name == "CONTENT_REVIEW_CACHE_DIR" and environment[name] == "exports/content_review_cache"
                ):
                    report.issues.append("machine-path-review:" + name)
            # All non-comment lines have to pass the whitelist before any bytes are copied.
            selected_files[Path("config/.env")] = source

    feishu = raw_config.get("feishu", {})
    stores = raw_config.get("stores", {})
    review = raw_config.get("content_review", {})
    if not all(isinstance(section, dict) for section in (feishu, stores, review)):
        raise UnsafeImport("invalid-config-section")
    bitable = feishu.get("bitable", {})
    if not isinstance(bitable, dict):
        raise UnsafeImport("invalid-config-section")
    required = {
        "stores.default_store_id": environment.get("ZN_SAMPLE_STORE_ID") or stores.get("default_store_id"),
        "stores.prepare_store_id": environment.get("ZN_SAMPLE_PREPARE_STORE_ID") or stores.get("prepare_store_id"),
        "feishu.app_id": environment.get("FEISHU_APP_ID") or feishu.get("app_id"),
        "feishu.app_secret": environment.get("FEISHU_APP_SECRET") or feishu.get("app_secret"),
        "feishu.bitable.app_id": environment.get("FEISHU_BITABLE_APP_ID") or bitable.get("app_id"),
        "feishu.bitable.app_secret": environment.get("FEISHU_BITABLE_APP_SECRET") or bitable.get("app_secret"),
        "LLM_API_KEY": environment.get("LLM_API_KEY"),
    }
    enabled = review.get("enabled", environment.get("CONTENT_REVIEW_ENABLED", "true"))
    if enabled not in (False, "false", "False", "0"):
        required.update({
            "TIKHUB_API_KEY": review.get("tikhub_api_key") or environment.get("TIKHUB_API_KEY"),
            "ARK_API_KEY": review.get("ark_api_key") or environment.get("ARK_API_KEY"),
        })
    report.missing_fields = sorted(name for name, value in required.items() if not isinstance(value, str) or not value.strip())
    return selected_files


def _is_process_alive(process_id: int) -> bool | None:
    try:
        os.kill(process_id, 0)
    except OSError as error:
        return False if error.errno == errno.ESRCH else None
    except (ValueError, OverflowError):
        return None
    return True


def inspect_local_processes() -> list[str]:
    """Inspect entrypoints only; never include command lines in the report."""
    if sys.platform == "win32":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        executable = str(Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        command = [executable, "-NoProfile", "-NonInteractive", "-Command",
                   "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"]
    else:
        command = ["/bin/ps", "-axo", "pid=,command="]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
        if sys.platform == "win32":
            processes = json.loads(result.stdout)
            if not isinstance(processes, list):
                raise ValueError("invalid process inventory")
            rows = []
            for process in processes:
                if not process.get("CommandLine") and re.search(r"python|node", str(process.get("Name")), re.I):
                    return ["process-state-unknown"]
                rows.append((int(process["ProcessId"]), str(process.get("CommandLine") or "")))
        else:
            rows = [(int(line.strip().split(None, 1)[0]), line.strip().split(None, 1)[1])
                    for line in result.stdout.splitlines() if line.strip()]
        return ["independent-entrypoint-running"] if any(
            process_id != os.getpid() and ENTRYPOINT_PATTERN.search(command_line)
            for process_id, command_line in rows
        ) else []
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, IndexError):
        return ["process-state-unknown"]


def _check_instance_state(target_root: Path) -> None:
    state_path = _safe_path(target_root / "runtime/state.json", target_root)
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            process_id = state["pid"]
            if type(process_id) is not int or process_id <= 0:
                raise ValueError("invalid pid")
        except (ValueError, KeyError, TypeError):
            raise UnsafeImport("instance-state-unknown") from None
        if _is_process_alive(process_id) is not False:
            raise UnsafeImport("instance-running-or-unknown")


def _check_existing_lock(target_root: Path) -> None:
    """Dry-run can inspect an existing lock, but never create/reclaim one."""
    lock_path = _safe_path(target_root / "runtime/instance.lock", target_root)
    if not lock_path.exists():
        return
    if sys.platform == "win32":
        raise UnsafeImport("instance-lock-busy")
    import fcntl

    with lock_path.open("rb") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UnsafeImport("instance-lock-busy") from None
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _maintenance_lock(target_root: Path):
    """Same protocol/path as lifecycle.InstanceLock; never reclaim stale locks.

    On POSIX both use flock. Windows uses exclusive file creation; an existing
    file is conservatively refused, even if its owner may have exited.
    """
    lock_path = _safe_path(target_root / "runtime/instance.lock", target_root)
    _private_directory(lock_path.parent)
    descriptor = None
    acquired = False
    try:
        if sys.platform == "win32":
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise UnsafeImport("instance-lock-busy") from None
            acquired = True
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        else:
            import fcntl

            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise UnsafeImport("instance-lock-busy") from None
            acquired = True
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if acquired and sys.platform == "win32":
            lock_path.unlink()


def _read_database(target_root: Path, *, apply: bool) -> sqlite3.Connection | None:
    database = _safe_path(target_root / "assistant.sqlite3", target_root)
    if not database.exists():
        return None
    for suffix in ("-wal", "-shm", "-journal"):
        _safe_path(Path(str(database) + suffix), target_root)
    if database.stat().st_size > MAX_DATABASE_BYTES:
        raise UnsafeImport("database-size-limit")
    journal_path = Path(str(database) + "-journal")
    if journal_path.exists() and journal_path.stat().st_size:
        raise UnsafeImport("database-journal-needs-recovery")
    wal_path = Path(str(database) + "-wal")
    if wal_path.exists() and wal_path.stat().st_size > MAX_DATABASE_BYTES:
        raise UnsafeImport("database-size-limit")
    if not apply and wal_path.exists() and wal_path.stat().st_size:
        # immutable mode would silently ignore WAL. Dry-run must not create or
        # mutate shm files, so defer this check until an offline apply session.
        raise UnsafeImport("database-wal-needs-offline-check")
    connection = sqlite3.connect(database.as_uri() + ("?mode=ro" if apply else "?mode=ro&immutable=1"), uri=True, timeout=1)
    snapshot = sqlite3.connect(":memory:")
    try:
        started_at = time.monotonic()

        def check_backup_budget(status: int, remaining: int, total: int) -> None:
            if time.monotonic() - started_at > DATABASE_READ_TIMEOUT_SECONDS:
                raise UnsafeImport("database-read-timeout")

        connection.backup(snapshot, pages=256, progress=check_backup_budget, sleep=0.05)
        if snapshot.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise UnsafeImport("database-unreadable")
        tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "jobs" in tables:
            columns = {row[1] for row in snapshot.execute("PRAGMA table_info(jobs)")}
            if "status" not in columns:
                raise UnsafeImport("database-job-schema-unknown")
            if snapshot.execute("SELECT count(*) FROM jobs WHERE status IN ('pending','running')").fetchone()[0]:
                raise UnsafeImport("database-active-jobs")
            if "error_code" in columns and snapshot.execute(
                "SELECT count(*) FROM jobs WHERE error_code LIKE '%unknown%' OR error_code LIKE '%uncertain%'"
            ).fetchone()[0]:
                raise UnsafeImport("database-uncertain-writes")
            if "job_type" in columns:
                placeholders = ",".join("?" for _ in WRITE_JOB_TYPES)
                interrupted_writes = snapshot.execute(
                    f"SELECT count(*) FROM jobs WHERE status='interrupted' AND job_type IN ({placeholders})",
                    tuple(WRITE_JOB_TYPES),
                ).fetchone()[0]
            else:
                interrupted_writes = snapshot.execute("SELECT count(*) FROM jobs WHERE status='interrupted'").fetchone()[0]
            if interrupted_writes:
                raise UnsafeImport("database-uncertain-writes")
        if "followup_tasks" in tables:
            columns = {row[1] for row in snapshot.execute("PRAGMA table_info(followup_tasks)")}
            if "send_result" not in columns:
                raise UnsafeImport("database-followup-schema-unknown")
            if snapshot.execute(
                "SELECT count(*) FROM followup_tasks WHERE send_result IN ('sending','send-unknown','write-uncertain')"
            ).fetchone()[0]:
                raise UnsafeImport("database-uncertain-writes")
        for table_name, status_columns in {
            "auto_approval_executions": ("status", "error_code"),
            "auto_approval_execution_items": ("approve_status", "feishu_relation_status", "platform_confirmation_status"),
        }.items():
            if table_name not in tables:
                continue
            columns = {row[1] for row in snapshot.execute(f"PRAGMA table_info({table_name})")}
            for column_name in status_columns:
                if column_name not in columns:
                    raise UnsafeImport("database-execution-schema-unknown")
                if snapshot.execute(
                    f"SELECT count(*) FROM {table_name} WHERE {column_name} LIKE '%unknown%' "
                    f"OR {column_name} LIKE '%uncertain%' OR {column_name} IN ('queued','running','sending')"
                ).fetchone()[0]:
                    raise UnsafeImport("database-uncertain-writes")
        return snapshot
    except BaseException:
        snapshot.close()
        raise
    finally:
        connection.close()


def _private_directory(path: Path) -> None:
    if not path.parent.exists():
        _private_directory(path.parent)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2).encode("utf-8") + b"\n"


def _target_status(path: Path, target_root: Path, digest: str) -> str:
    _safe_path(path, target_root)
    if not path.exists():
        return "planned"
    if not path.is_file():
        return "conflict"
    with path.open("rb") as existing:
        return "unchanged" if hashlib.file_digest(existing, "sha256").hexdigest() == digest else "conflict"


def _publish_file(staged: Path, destination: Path, target_root: Path) -> None:
    _safe_path(destination, target_root)
    _private_directory(destination.parent)
    # Hard-link publication is atomic and, unlike replace(), cannot overwrite
    # a user file that appeared after preflight. Staging is on the same volume.
    try:
        os.link(staged, destination)
    except FileExistsError:
        if _target_status(destination, target_root, hashlib.sha256(staged.read_bytes()).hexdigest()) != "unchanged":
            raise UnsafeImport("target-changed-conflict") from None


def import_local_state(
    source_root: Path,
    *,
    target_root: Path | None = None,
    apply: bool = False,
    yes: bool = False,
    process_inspector: Callable[[], list[str]] = inspect_local_processes,
    lock_first_startup_ready: bool | None = None,
) -> ImportReport:
    """Return a redacted report. Injectable roots/readiness are library-only.

    Never run this against real data before 009, even with the maintenance lock:
    an older bootstrap can still migrate before it attempts that lock.
    """
    source_root = Path(source_root).expanduser()
    target_root = Path(target_root) if target_root is not None else user_data_dir()
    report = ImportReport(str(source_root), str(target_root))
    database_snapshot = None
    try:
        if not source_root.is_absolute() or not target_root.is_absolute():
            raise UnsafeImport("absolute-roots-required")
        _safe_path(source_root, source_root)
        _safe_path(target_root, target_root)
        source_root = source_root.resolve(strict=True)
        target_root = target_root.resolve()
        report.source, report.target = str(source_root), str(target_root)
        if not source_root.is_dir() or source_root == target_root or target_root.is_relative_to(source_root) or source_root.is_relative_to(target_root):
            raise UnsafeImport("overlapping-roots")
        if apply and not yes:
            raise UnsafeImport("apply-requires-yes")
        files = _inventory(source_root)
        configuration = _validate_configuration(files, report)
        source_id = hashlib.sha256(str(source_root).encode("utf-8")).hexdigest()[:24]
        archive_root = target_root / "legacy" / source_id
        destinations = {target_root / relative_path: source for relative_path, source in configuration.items()}
        destinations.update({archive_root / source.relative_path: source for source in files})
        manifest = {
            "schema_version": 1, "source": str(source_root),
            "files": [{"path": source.relative_path, "sha256": source.sha256, "size": source.size,
                       "category": source.category} for source in files],
        }
        manifest_path = archive_root / "import-manifest.json"
        manifest_bytes = _canonical(manifest)
        registry_path = target_root / "config/historical-exports.json"
        _safe_path(registry_path, target_root)
        registry = {"schema_version": 2, "archives": []}
        if registry_path.exists():
            if registry_path.stat().st_size > MAX_CONFIG_BYTES:
                raise UnsafeImport("history-registry-size-limit")
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            if not isinstance(registry, dict) or set(registry) != {"schema_version", "archives"} or registry["schema_version"] != 2 or not isinstance(registry["archives"], list):
                raise UnsafeImport("history-registry-needs-review")
            from assistant.paths import verify_historical_archives

            verify_historical_archives(registry)
        registration = {"root": str(archive_root), "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()}
        existing_registration = next((entry for entry in registry["archives"] if entry.get("root") == str(archive_root)), None)
        if existing_registration is not None and existing_registration != registration:
            raise UnsafeImport("archive-content-conflict")
        if existing_registration is None:
            registry["archives"].append(registration)
        generated = {manifest_path: manifest_bytes}
        for destination, source in destinations.items():
            status = _target_status(destination, target_root, source.sha256)
            report.entries.append({"source": str(source.path), "target": str(destination), "category": source.category, "result": status})
        for destination, content in generated.items():
            report.entries.append({"source": str(source_root), "target": str(destination), "category": "manifest",
                                   "result": _target_status(destination, target_root, hashlib.sha256(content).hexdigest())})
        if any(entry["result"] == "conflict" for entry in report.entries):
            report.result = "conflict"
            return report
        if report.issues or report.missing_fields:
            report.result = "blocked"
            return report
        _check_instance_state(target_root)
        _check_existing_lock(target_root)
        process_issues = process_inspector()
        if process_issues:
            raise UnsafeImport(process_issues[0])
        existing_parent = target_root
        while not existing_parent.exists():
            existing_parent = existing_parent.parent
        required_bytes = (
            sum(source.size for source in destinations.values()) * 2
            + len(manifest_bytes) * 2 + len(_canonical(registry)) * 2 + 1024 * 1024
        )
        database_path = target_root / "assistant.sqlite3"
        if database_path.exists():
            required_bytes += database_path.stat().st_size * 2
        if shutil.disk_usage(existing_parent).free < required_bytes:
            raise UnsafeImport("insufficient-disk-space")
        if not apply:
            database_snapshot = _read_database(target_root, apply=False)
            _check_stable(files, source_root)
            report.result = "unchanged" if existing_registration is not None and all(entry["result"] == "unchanged" for entry in report.entries) else "planned"
            return report
        ready = LOCK_FIRST_STARTUP_READY if lock_first_startup_ready is None else lock_first_startup_ready
        if not ready:
            raise UnsafeImport("release-lifecycle-not-ready-009")
        with _maintenance_lock(target_root):
            _check_instance_state(target_root)
            process_issues = process_inspector()
            if process_issues:
                raise UnsafeImport(process_issues[0])
            database_snapshot = _read_database(target_root, apply=True)
            _check_stable(files, source_root)
            if existing_registration is not None and all(entry["result"] == "unchanged" for entry in report.entries):
                report.result = "unchanged"
                return report
            _private_directory(target_root)
            with tempfile.TemporaryDirectory(prefix=".state-import-", dir=target_root) as temporary_directory:
                staging_root = Path(temporary_directory)
                staged_files = {}
                for index, (destination, source) in enumerate(destinations.items()):
                    staged = staging_root / str(index)
                    descriptor = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with source.path.open("rb") as original, os.fdopen(descriptor, "wb") as output:
                        shutil.copyfileobj(original, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                    with staged.open("rb") as copied:
                        if hashlib.file_digest(copied, "sha256").hexdigest() != source.sha256:
                            raise UnsafeImport("source-changing")
                    staged_files[destination] = staged
                _check_stable(files, source_root)
                # Detect added/deleted files as well as edits before publication.
                if _inventory(source_root) != files:
                    raise UnsafeImport("source-changing")
                for index, (destination, content) in enumerate(generated.items()):
                    staged = staging_root / f"generated-{index}"
                    _write_private(staged, content)
                    staged_files[destination] = staged
                if database_snapshot is not None:
                    backup_directory = target_root / "backups" / ("state-import-" + source_id)
                    _private_directory(backup_directory)
                    staged_backup = staging_root / "database.sqlite3"
                    _write_private(staged_backup, b"")
                    with sqlite3.connect(staged_backup) as backup:
                        database_snapshot.backup(backup)
                    with staged_backup.open("rb") as backup:
                        digest = hashlib.file_digest(backup, "sha256").hexdigest()
                    _publish_file(staged_backup, backup_directory / (digest + ".sqlite3"), target_root)
                for destination, staged in staged_files.items():
                    _publish_file(staged, destination, target_root)
                _safe_path(archive_root / "exports", target_root)
                _private_directory(archive_root / "exports")
                from assistant.paths import verify_historical_archives

                verify_historical_archives(registry)
                # Registration is the completion marker, published last. Existing
                # registrations are extended only under the shared instance lock.
                registry_staged = staging_root / "registry.json"
                _write_private(registry_staged, _canonical(registry))
                if registry_path.exists():
                    current_registry = json.loads(registry_path.read_text(encoding="utf-8"))
                    expected = {"schema_version": 2, "archives": [entry for entry in registry["archives"] if entry != registration]}
                    if current_registry != expected:
                        raise UnsafeImport("history-registry-changed")
                    os.replace(registry_staged, registry_path)
                else:
                    _publish_file(registry_staged, registry_path, target_root)
            for entry in report.entries:
                if entry["result"] == "planned":
                    entry["result"] = "imported"
            report.result = "imported"
            return report
    except (UnsafeImport, ReleasePathError) as error:
        diagnostic_code = error.code if isinstance(error, ReleasePathError) else str(error)
        report.result = "conflict" if diagnostic_code.endswith("conflict") else "blocked"
        report.issues.append(diagnostic_code)
    except (tomllib.TOMLDecodeError, UnicodeError, ValueError, TypeError, sqlite3.DatabaseError):
        report.result = "blocked"
        report.issues.append("local-data-invalid")
    except (OSError, RuntimeError):
        # Exception text may contain TOML lines, credentials or subprocess data.
        report.result = "blocked"
        report.issues.append("local-io-failure")
    finally:
        if database_snapshot is not None:
            database_snapshot.close()
    return report
