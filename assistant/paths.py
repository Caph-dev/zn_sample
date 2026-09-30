"""Platform-specific paths for assistant-owned user data."""
from __future__ import annotations

import os
import sys
import hashlib
import json
import platform
import struct
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath


APP_DIR_NAME = "ZnSampleAssistant"
USER_SUBDIRECTORIES = ("config", "exports", "logs", "backups", "runtime")


class ReleasePathError(RuntimeError):
    """Fail closed on invalid release resources without exposing configuration."""

    def __init__(self, code: str, summary: str) -> None:
        self.code = code
        super().__init__(f"{code}: {summary}")


def application_resource_dir() -> Path:
    """Locate source resources independently of cwd and launcher environment."""
    return Path(__file__).resolve().parents[1]


def _contained_resource_path(bundle_root: Path, relative_path: object) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ReleasePathError("release-path-invalid", "发布清单资源路径无效。")
    posix_path = PurePosixPath(relative_path)
    windows_path = PureWindowsPath(relative_path)
    if (
        posix_path.is_absolute() or windows_path.drive or windows_path.root
        or ".." in posix_path.parts or "\\" in relative_path
    ):
        raise ReleasePathError("release-path-escape", "发布清单资源路径越界。")
    try:
        resolved_path = (bundle_root / relative_path).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise ReleasePathError("release-path-invalid", "发布资源路径或链接无效。") from error
    if not resolved_path.is_relative_to(bundle_root.resolve()):
        raise ReleasePathError("release-path-escape", "发布清单资源链接越界。")
    return resolved_path


def _release_manifest() -> tuple[Path, dict] | None:
    resource_root = application_resource_dir()
    manifest_path = resource_root.parent / "release-manifest.json"
    if not manifest_path.exists() and not manifest_path.is_symlink():
        return None
    bundle_root = resource_root.parent.resolve()
    if manifest_path.is_symlink() or resource_root != bundle_root / "app":
        raise ReleasePathError("release-layout-invalid", "发布资源必须位于包内 app 目录。")
    try:
        manifest_stat = manifest_path.stat()
    except OSError as error:
        raise ReleasePathError("release-manifest-invalid", "无法读取发布清单。") from error
    manifest = _read_release_manifest(
        bundle_root, manifest_stat.st_mtime_ns, manifest_stat.st_ctime_ns,
        manifest_stat.st_size, manifest_stat.st_ino,
    )
    sys.dont_write_bytecode = True
    return bundle_root, manifest


@lru_cache(maxsize=4)
def _read_release_manifest(
    bundle_root: Path, modified_at: int, changed_at: int, size: int, inode: int,
) -> dict:
    # Cache only the immutable manifest, not executable hashes or tool paths.
    manifest_path = bundle_root / "release-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReleasePathError("release-manifest-invalid", "无法读取发布清单。") from error
    if (
        not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or not isinstance(manifest.get("target"), str)
        or manifest.get("target") not in {"macos-arm64", "windows-x64"}
        or not isinstance(manifest.get("executables"), dict)
        or not isinstance(manifest.get("files"), list)
    ):
        raise ReleasePathError("release-manifest-invalid", "发布清单结构或目标无效。")
    for relative_path in manifest["executables"].values():
        _contained_resource_path(bundle_root, relative_path)
    seen_paths = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ReleasePathError("release-manifest-invalid", "发布文件清单无效。")
        _contained_resource_path(bundle_root, entry.get("path"))
        if entry["path"] in seen_paths:
            raise ReleasePathError("release-manifest-invalid", "发布文件清单含重复路径。")
        seen_paths.add(entry["path"])
        if "symlink" in entry:
            if not isinstance(entry["symlink"], str):
                raise ReleasePathError("release-manifest-invalid", "发布链接清单无效。")
            link_value = entry["symlink"]
            if PurePosixPath(link_value).is_absolute() or PureWindowsPath(link_value).drive or "\\" in link_value:
                raise ReleasePathError("release-path-escape", "发布链接目标必须是包内相对路径。")
            link_target = PurePosixPath(entry["path"]).parent / link_value
            _contained_resource_path(bundle_root, os.path.normpath(str(link_target)))
        elif (
            not isinstance(entry.get("sha256"), str)
            or len(entry["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in entry["sha256"])
        ):
            raise ReleasePathError("release-manifest-invalid", "发布文件缺少可信哈希。")
    return manifest


def is_packaged_distribution() -> bool:
    return _release_manifest() is not None


def configuration_dir() -> Path:
    return user_data_dir() / "config" if is_packaged_distribution() else application_resource_dir()


def exports_dir(*, development_root: Path | None = None) -> Path:
    if is_packaged_distribution():
        return user_exports_dir()
    return (development_root or application_resource_dir()) / "exports"


def logs_dir(*, development_root: Path | None = None) -> Path:
    if is_packaged_distribution():
        return user_logs_dir()
    return (development_root or application_resource_dir()) / "logs"


def user_exports_dir() -> Path:
    """The console already owns user exports, including in source mode."""
    return user_data_dir() / "exports"


def user_logs_dir() -> Path:
    return user_data_dir() / "logs"


def content_review_cache_dir() -> Path:
    return exports_dir() / "content_review_cache"


def validate_writable_path(value: str | Path) -> Path:
    """Preserve explicit destinations, but never write into a release bundle."""
    path = Path(value)
    if is_packaged_distribution() and path.resolve().is_relative_to(application_resource_dir().parent.resolve()):
        raise ReleasePathError("release-output-readonly", "输出不能写入安装目录，请选择用户数据目录。")
    return path


def resolve_content_cache_path(value: str | Path, *, development_root: Path | None = None) -> Path:
    root = user_data_dir() if is_packaged_distribution() else (development_root or application_resource_dir())
    resolved_path = (root / Path(value).expanduser()).resolve()
    if is_packaged_distribution():
        bundle_root = application_resource_dir().parent.resolve()
        if resolved_path.is_relative_to(bundle_root):
            raise ReleasePathError("release-cache-readonly", "审核缓存不能写入安装目录。")
    return resolved_path


def historical_export_dirs() -> tuple[Path, ...]:
    """Read only explicitly registered roots; no discovery or migration here."""
    registry_path = configuration_dir() / "historical-exports.json"
    if not registry_path.exists() and not registry_path.is_symlink():
        return ()
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(registry, dict) or set(registry) != {"schema_version", "directories"}:
            raise ValueError("invalid registry")
        if registry["schema_version"] != 1 or not isinstance(registry["directories"], list):
            raise ValueError("invalid registry")
        directories = []
        for value in registry["directories"]:
            if not isinstance(value, str) or not Path(value).expanduser().is_absolute():
                raise ValueError("absolute root required")
            directory = Path(value).expanduser().resolve()
            if not directory.is_dir():
                raise ValueError("registered history missing")
            if directory not in directories:
                directories.append(directory)
        return tuple(directories)
    except (OSError, ValueError) as error:
        raise ReleasePathError("release-history-invalid", "登记的历史导出目录不可用，禁止当作未发送。") from error


def _verify_tool_architecture(tool_path: Path, target: str) -> None:
    with tool_path.open("rb") as source:
        header = source.read(64)
        if target == "windows-x64" and len(header) >= 64 and header[:2] == b"MZ":
            source.seek(struct.unpack_from("<I", header, 60)[0])
            if source.read(6) == b"PE\0\0\x64\x86":
                return
        if target == "macos-arm64" and len(header) >= 32:
            if header[:4] == b"\xcf\xfa\xed\xfe" and struct.unpack_from("<I", header, 4)[0] == 0x0100000C:
                return
    raise ReleasePathError("release-tool-architecture", "发布工具架构不符合目标平台。")


def bundled_tool_path(tool_name: str) -> Path:
    """Validate a manifest-bound private executable; never consult PATH."""
    release = _release_manifest()
    if release is None:
        raise ReleasePathError("release-required", "当前不是发布目录包。")
    bundle_root, manifest = release
    if tool_name not in {"python", "node", "ffmpeg"}:
        raise ReleasePathError("release-tool-unknown", "未登记的发布工具。")
    target = manifest["target"]
    expected_host = ("darwin", {"arm64", "aarch64"}) if target == "macos-arm64" else ("win32", {"amd64", "x86_64"})
    if sys.platform != expected_host[0] or platform.machine().lower() not in expected_host[1]:
        raise ReleasePathError("release-host-architecture", "发布目标与当前系统架构不一致。")
    relative_path = manifest["executables"].get(f"{tool_name}_executable")
    tool_path = _contained_resource_path(bundle_root, relative_path)
    if (
        not PurePosixPath(relative_path).is_relative_to(f"runtime/{tool_name}")
        or not tool_path.is_relative_to(bundle_root / "runtime" / tool_name)
    ):
        raise ReleasePathError("release-tool-layout", "发布工具必须位于对应私有运行时目录。")
    if not tool_path.is_file():
        raise ReleasePathError("release-tool-missing", "包内工具缺失，请重新安装完整目录包。")
    resolved_relative_path = tool_path.relative_to(bundle_root).as_posix()
    entry = next((entry for entry in manifest["files"] if entry["path"] == resolved_relative_path), None)
    if entry is None or "sha256" not in entry:
        raise ReleasePathError("release-tool-unlisted", "包内工具缺少文件校验记录。")
    with tool_path.open("rb") as source:
        actual_digest = hashlib.file_digest(source, "sha256").hexdigest()
    if actual_digest != entry["sha256"]:
        raise ReleasePathError("release-tool-hash", "包内工具校验失败，请重新安装完整目录包。")
    _verify_tool_architecture(tool_path, target)
    return tool_path


def assert_private_interpreter(executable: str | None = None) -> None:
    if is_packaged_distribution() and Path(executable or sys.executable).resolve() != bundled_tool_path("python"):
        raise ReleasePathError("release-python-required", "发布任务必须使用包内 Python 解释器。")


def python_subprocess_environment(parent_environment: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if parent_environment is None else parent_environment)
    if is_packaged_distribution():
        assert_private_interpreter()
        for name in tuple(environment):
            if name.startswith(("PYTHON", "NODE_", "DYLD_", "LD_")) or name in {"VIRTUAL_ENV", "CONDA_PREFIX", "__PYVENV_LAUNCHER__"}:
                environment.pop(name)
        environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return environment


def user_data_dir() -> Path:
    """Return the platform-standard application data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def ensure_user_dirs() -> Path:
    """Create all assistant-owned directories and return the application root."""
    application_directory = user_data_dir()
    application_directory.mkdir(parents=True, exist_ok=True)
    for subdirectory_name in USER_SUBDIRECTORIES:
        (application_directory / subdirectory_name).mkdir(parents=True, exist_ok=True)
    return application_directory


def database_path() -> Path:
    return user_data_dir() / "assistant.sqlite3"


def runtime_dir() -> Path:
    return user_data_dir() / "runtime"
