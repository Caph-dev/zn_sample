"""Offline release regressions using temporary resources, user data and binaries.

Windows cases exercise the branch with real host filesystem paths; they are not
native Windows acceptance. No fixture launches tools or reads operator settings.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant import paths  # noqa: E402


def make_native_binary(target: str, *, wrong_architecture: bool = False) -> bytes:
    """Build thin executable headers, not scripts with executable filenames."""
    if target == "macos-arm64":
        architecture = 0x01000007 if wrong_architecture else 0x0100000C
        return struct.pack("<8I", 0xFEEDFACF, architecture, 0, 2, 0, 0, 0, 0)
    header = bytearray(128)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 64)
    header[64:68] = b"PE\0\0"
    struct.pack_into("<H", header, 68, 0x014C if wrong_architecture else 0x8664)
    return bytes(header)


@dataclass
class ReleaseSandbox:
    resource_root: Path
    user_root: Path
    working_directory: Path
    manifest: dict | None = None

    @property
    def bundle_root(self) -> Path:
        return self.resource_root.parent

    def write_manifest(self) -> None:
        (self.bundle_root / "release-manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    def tool_path(self, tool_name: str) -> Path:
        assert self.manifest is not None
        return self.bundle_root / self.manifest["executables"][f"{tool_name}_executable"]

    def register_tool_hash(self, tool_name: str) -> None:
        tool_path = self.tool_path(tool_name)
        relative_path = tool_path.relative_to(self.bundle_root).as_posix()
        assert self.manifest is not None
        for entry in self.manifest["files"]:
            if entry["path"] == relative_path:
                entry["sha256"] = hashlib.sha256(tool_path.read_bytes()).hexdigest()
                self.write_manifest()
                return
        raise AssertionError("Fixture tool must already have a manifest entry")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    resource_root = tmp_path / "\u5b89\u88c5\u76ee\u5f55 with spaces" / "app"
    resource_root.mkdir(parents=True)
    user_root = tmp_path / "\u64cd\u4f5c\u5458 user data"
    working_directory = tmp_path / "\u4efb\u610f\u5de5\u4f5c\u76ee\u5f55 unrelated cwd"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(paths, "application_resource_dir", lambda: resource_root)
    monkeypatch.setattr(paths, "user_data_dir", lambda: user_root)
    # Replacing the module's sys reference avoids changing pytest's host platform.
    monkeypatch.setattr(
        paths,
        "sys",
        SimpleNamespace(
            platform="darwin", executable=sys.executable, dont_write_bytecode=False
        ),
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda path_class: tmp_path / "home"))
    for environment_name in tuple(os.environ):
        if environment_name.startswith(("ZN_SAMPLE_", "CONTENT_REVIEW_")) or environment_name in {
            "TIKHUB_API_KEY", "ARK_API_KEY", "ARK_MODEL", "FFMPEG_PATH"
        }:
            monkeypatch.delenv(environment_name)
    return ReleaseSandbox(resource_root, user_root, working_directory)


@pytest.fixture(params=["macos-arm64", "windows-x64"])
def release(sandbox, monkeypatch, request):
    target = request.param
    monkeypatch.setattr(paths.sys, "platform", "darwin" if target == "macos-arm64" else "win32")
    monkeypatch.setattr(paths.platform, "machine", lambda: "arm64" if target == "macos-arm64" else "AMD64")
    sandbox.manifest = {
        "schema_version": 1,
        "target": target,
        "executables": {},
        "files": [],
    }
    for tool_name in ("python", "node", "ffmpeg"):
        filename = f"{tool_name}.exe" if target == "windows-x64" else tool_name
        relative_path = f"runtime/{tool_name}/bin/{filename}"
        executable_path = sandbox.bundle_root / relative_path
        executable_path.parent.mkdir(parents=True)
        content = make_native_binary(target)
        executable_path.write_bytes(content)
        sandbox.manifest["executables"][f"{tool_name}_executable"] = relative_path
        sandbox.manifest["files"].append({
            "path": relative_path, "sha256": hashlib.sha256(content).hexdigest()
        })
    sandbox.write_manifest()
    return sandbox


@pytest.fixture(params=["lib.app_config", "scripts.lib.app_config"])
def config_module(sandbox, monkeypatch, request):
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    module = importlib.import_module(request.param)
    monkeypatch.setattr(module, "PROJECT_ROOT", sandbox.resource_root)
    monkeypatch.setattr(module, "DEFAULT_CONFIG_PATHS", (
        sandbox.resource_root / "config.toml",
        sandbox.resource_root / "config" / "config.toml",
    ))
    monkeypatch.setattr(module, "DEFAULT_ENV_PATH", sandbox.resource_root / ".env")
    module.clear_config_cache()
    yield module
    module.clear_config_cache()


@pytest.mark.parametrize("host_platform,use_local_app_data", [
    ("darwin", False), ("win32", True), ("win32", False),
])
def test_standard_user_directory_stays_platform_specific_without_creating_it(
    tmp_path, monkeypatch, host_platform, use_local_app_data
):
    home_directory = tmp_path / "\u7528\u6237 home"
    local_app_data = tmp_path / "\u672c\u5730 application data"
    monkeypatch.setattr(paths, "sys", SimpleNamespace(platform=host_platform))
    monkeypatch.setattr(Path, "home", classmethod(lambda path_class: home_directory))
    if use_local_app_data:
        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    else:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    if host_platform == "darwin":
        expected_root = home_directory / "Library/Application Support/ZnSampleAssistant"
    elif use_local_app_data:
        expected_root = local_app_data / "ZnSampleAssistant"
    else:
        expected_root = home_directory / "AppData/Local/ZnSampleAssistant"
    assert paths.user_data_dir() == expected_root
    assert paths.database_path() == expected_root / "assistant.sqlite3"
    assert not home_directory.exists()
    assert not local_app_data.exists()


def test_development_defaults_ignore_cwd_and_do_not_create_user_data(sandbox):
    # A coincidental marker in cwd must not turn source execution into a release.
    (sandbox.working_directory / "release-manifest.json").write_text("{}")
    assert not paths.is_packaged_distribution()
    assert paths.configuration_dir() == sandbox.resource_root
    assert paths.exports_dir() == sandbox.resource_root / "exports"
    assert paths.logs_dir() == sandbox.resource_root / "logs"
    assert paths.content_review_cache_dir() == sandbox.resource_root / "exports/content_review_cache"
    development_root = sandbox.working_directory / "explicit project"
    assert paths.exports_dir(development_root=development_root) == development_root / "exports"
    assert paths.logs_dir(development_root=development_root) == development_root / "logs"
    assert not sandbox.user_root.exists()


def test_release_defaults_use_user_data_without_mkdir_or_installation_writes(release):
    installation_files = set(release.bundle_root.rglob("*"))
    assert paths.is_packaged_distribution()
    assert paths.configuration_dir() == release.user_root / "config"
    assert paths.exports_dir(development_root=release.resource_root) == release.user_root / "exports"
    assert paths.logs_dir(development_root=release.resource_root) == release.user_root / "logs"
    assert paths.content_review_cache_dir() == release.user_root / "exports/content_review_cache"
    assert paths.database_path() == release.user_root / "assistant.sqlite3"
    assert paths.runtime_dir() == release.user_root / "runtime"
    assert paths.sys.dont_write_bytecode is True
    assert not release.user_root.exists()
    assert set(release.bundle_root.rglob("*")) == installation_files


def test_release_reports_and_latest_use_user_exports_with_readonly_installation(release, monkeypatch):
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    from scripts.lib import export_util, operator_launch
    from assistant.services.export_service import ExportService

    installation_files = set(release.bundle_root.rglob("*"))
    original_mode = release.resource_root.stat().st_mode
    release.resource_root.chmod(0o555)
    try:
        prefix = operator_launch.default_out_prefix("screen")
        written_reports = export_util.write_reports([], prefix)
        assert prefix.parent == release.user_root / "exports"
        assert export_util.resolve_from_export_arg("latest") == written_reports["json"]
        explicit_prefix = release.working_directory / "explicit reports" / "sample_screen_custom"
        assert export_util.write_reports([], explicit_prefix)["json"] == explicit_prefix.with_suffix(".json")

        export_service = ExportService(None)
        monkeypatch.setattr(export_service, "_rows", lambda kind: [])
        assert export_service.export("today").parent == release.user_root / "exports"
        assert set(release.bundle_root.rglob("*")) == installation_files
    finally:
        release.resource_root.chmod(original_mode)


@pytest.mark.parametrize("output_location", ["absolute", "relative", "symlink"])
def test_explicit_report_destination_cannot_write_into_release_bundle(release, monkeypatch, output_location):
    from scripts.lib import export_util

    if output_location == "absolute":
        prefix = release.resource_root / "exports/sample_screen_explicit"
    elif output_location == "relative":
        prefix = Path(os.path.relpath(release.resource_root / "exports/sample_screen_explicit"))
    else:
        linked_directory = release.working_directory / "linked output"
        linked_directory.symlink_to(release.resource_root, target_is_directory=True)
        prefix = linked_directory / "exports/sample_screen_explicit"
    for writer in (export_util.write_reports, export_util.write_generic_reports):
        with pytest.raises(paths.ReleasePathError, match="release-output-readonly"):
            writer([], prefix)
    assert not (release.resource_root / "exports").exists()


@pytest.mark.parametrize("script_name,extra_arguments", [
    ("send_sample_intro", []), ("sync_shipped_tracking", []),
    ("auto_approval", ["--mode", "preview", "--store-id", "store-test"]),
])
def test_invalid_explicit_output_is_rejected_before_platform_access(release, monkeypatch, script_name, extra_arguments):
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    script = importlib.import_module(script_name)
    monkeypatch.setattr(sys, "argv", [
        script_name, *extra_arguments, "--out", str(release.resource_root / "report.json"),
    ])
    with patch("subprocess.run", side_effect=AssertionError("Platform must not be accessed")):
        with pytest.raises(paths.ReleasePathError, match="release-output-readonly"):
            script.main()
    assert not release.user_root.exists()


def test_release_fixed_children_retain_gates_interpreter_and_cancellation(release, monkeypatch):
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    from assistant.jobs.handlers import auto_approval, followup_send, operator
    from scripts.lib import operator_launch

    private_python = str(release.tool_path("python"))
    monkeypatch.setattr(paths.sys, "executable", private_python)
    monkeypatch.setattr(sys, "executable", private_python)
    monkeypatch.setattr(followup_send, "application_resource_dir", lambda: release.resource_root)
    monkeypatch.setattr(operator, "application_resource_dir", lambda: release.resource_root)
    approval_script = release.resource_root / "scripts/auto_approval.py"
    monkeypatch.setattr(auto_approval, "AUTO_APPROVAL_SCRIPT", approval_script)
    request_payload = {
        "store_id": "store-test", "result_path": str(release.user_root / "result.json"),
        "rules_path": str(release.user_root / "rules.json"),
        "preview_path": str(release.user_root / "preview.json"),
        "apply_ids_path": str(release.user_root / "apply_ids.json"),
        "backup_prefix": str(release.user_root / "backup"),
        "execution_id": "execution-test", "limit": 1, "write_feishu": True,
    }
    approval_arguments = auto_approval._fixed_argv(request_payload, "execute")
    assert approval_arguments[:2] == [private_python, str(approval_script)]
    assert approval_arguments[approval_arguments.index("--limit") + 1] == "1"
    assert approval_arguments[approval_arguments.index("--write-feishu") + 1] == "1"
    assert "--yes" in approval_arguments

    launch_arguments = operator_launch.build_step_argv(
        python=private_python,
        script=release.resource_root / "scripts/send_sample_intro.py",
        extra_args=("--execute", "--yes", "--execute-limit", "1"),
        out_prefix=release.user_root / "exports/intro",
    )
    assert launch_arguments[:2] == [private_python, str(release.resource_root / "scripts/send_sample_intro.py")]
    captured_invocations = []

    def capture_child(arguments, **options):
        captured_invocations.append((arguments, options))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(operator.subprocess, "run", capture_child)
    log_path = release.user_root / "logs/job.log"
    log_path.parent.mkdir(parents=True)
    cancel_flag = release.user_root / "runtime/job.cancel"
    assert operator._run_subprocess(launch_arguments, log_path=log_path, cancel_flag=cancel_flag) == 0
    assert followup_send._run_script(task_id=17, job_id="job-test", log_path=log_path) == 0
    for arguments, options in captured_invocations:
        assert arguments[0] == private_python
        assert options["cwd"] == str(release.resource_root)
        assert options["shell"] is False
        assert options["env"]["PYTHONIOENCODING"] == "utf-8"
        assert options["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert captured_invocations[0][1]["env"]["ZN_SAMPLE_CANCEL_FLAG"] == str(cancel_flag)
    assert captured_invocations[1][0] == [
        private_python, str(release.resource_root / "scripts/send_followup_message.py"),
        "--execute", "--yes", "--task-id", "17", "--ignore-job-id", "job-test",
    ]


def test_cache_paths_anchor_to_source_or_explicit_development_root(sandbox):
    relative_cache = Path("exports") / "\u5185\u5bb9\u5ba1\u6838 cache"
    assert paths.resolve_content_cache_path(relative_cache) == sandbox.resource_root / relative_cache
    assert paths.resolve_content_cache_path(
        relative_cache, development_root=sandbox.working_directory
    ) == sandbox.working_directory / relative_cache
    absolute_cache = sandbox.working_directory / "explicit cache"
    assert paths.resolve_content_cache_path(absolute_cache) == absolute_cache


def test_release_cache_paths_anchor_to_user_data_and_allow_external_explicit_paths(release):
    assert paths.resolve_content_cache_path(
        "custom/cache", development_root=release.resource_root
    ) == release.user_root / "custom/cache"
    external_cache = release.working_directory / "external cache"
    assert paths.resolve_content_cache_path(external_cache) == external_cache
    assert not release.user_root.exists()


@pytest.mark.parametrize("cache_location", ["absolute", "relative", "symlink"])
def test_release_cache_cannot_write_into_installation(release, cache_location):
    if cache_location == "absolute":
        cache_path = release.resource_root / "cache"
    elif cache_location == "relative":
        cache_path = Path(os.path.relpath(release.resource_root / "cache", release.user_root))
    else:
        cache_path = release.working_directory / "linked cache"
        cache_path.symlink_to(release.resource_root, target_is_directory=True)
    with pytest.raises(paths.ReleasePathError, match="release-cache-readonly"):
        paths.resolve_content_cache_path(cache_path)


@pytest.mark.parametrize("manifest_section", ["executables", "files"])
@pytest.mark.parametrize("unsafe_path", ["../outside", "/outside", "C:/outside/python.exe", "runtime/../../outside", "runtime\\python.exe"])
def test_manifest_rejects_cross_platform_absolute_paths_and_traversal(
    sandbox, manifest_section, unsafe_path
):
    sandbox.manifest = {
        "schema_version": 1, "target": "macos-arm64",
        "executables": {"python_executable": "runtime/python/bin/python"}, "files": [],
    }
    if manifest_section == "executables":
        sandbox.manifest["executables"]["python_executable"] = unsafe_path
    else:
        sandbox.manifest["files"] = [{"path": unsafe_path, "sha256": "0" * 64}]
    sandbox.write_manifest()
    with pytest.raises(paths.ReleasePathError, match="release-path-escape"):
        paths.is_packaged_distribution()


@pytest.mark.parametrize("manifest_text", ["not json", "[]", '{"schema_version": 2}', '{"schema_version": 1, "target": "macos-x64", "executables": {}, "files": []}'])
def test_broken_manifest_is_not_silently_treated_as_development(sandbox, manifest_text):
    (sandbox.bundle_root / "release-manifest.json").write_text(manifest_text)
    with pytest.raises(paths.ReleasePathError, match="release-manifest-invalid"):
        paths.exports_dir()


def test_manifest_in_wrong_resource_layout_is_rejected(sandbox, monkeypatch):
    other_resource_root = sandbox.bundle_root / "not-app"
    other_resource_root.mkdir()
    monkeypatch.setattr(paths, "application_resource_dir", lambda: other_resource_root)
    (sandbox.bundle_root / "release-manifest.json").write_text("{}")
    with pytest.raises(paths.ReleasePathError, match="release-layout-invalid"):
        paths.is_packaged_distribution()


def test_manifest_symlink_is_rejected_even_with_valid_contents(release):
    manifest_path = release.bundle_root / "release-manifest.json"
    external_manifest = release.working_directory / "manifest.json"
    manifest_path.rename(external_manifest)
    manifest_path.symlink_to(external_manifest)
    with pytest.raises(paths.ReleasePathError, match="release-layout-invalid"):
        paths.is_packaged_distribution()


def test_manifest_executable_symlink_cannot_escape_bundle(release):
    executable_path = release.tool_path("python")
    external_tool = release.working_directory / "python"
    executable_path.rename(external_tool)
    executable_path.symlink_to(external_tool)
    with pytest.raises(paths.ReleasePathError, match="release-path-escape"):
        paths.bundled_tool_path("python")


@pytest.mark.parametrize("tool_name", ["python", "node", "ffmpeg"])
def test_bundled_tools_are_hash_verified_native_executables_without_path_lookup(release, tool_name):
    with patch("shutil.which", side_effect=AssertionError("Global tools are forbidden")):
        assert paths.bundled_tool_path(tool_name) == release.tool_path(tool_name)


def test_bundled_tool_tampering_is_detected_on_subsequent_resolution(release):
    tool_path = paths.bundled_tool_path("node")
    tool_path.write_bytes(tool_path.read_bytes() + b"tampered")
    with pytest.raises(paths.ReleasePathError, match="release-tool-hash"):
        paths.bundled_tool_path("node")


@pytest.mark.parametrize("invalid_binary", ["wrong-architecture", "script"])
def test_matching_hash_does_not_make_wrong_architecture_or_script_trusted(release, invalid_binary):
    content = (
        make_native_binary(release.manifest["target"], wrong_architecture=True)
        if invalid_binary == "wrong-architecture" else b"#!/bin/sh\nexit 0\n"
    )
    release.tool_path("ffmpeg").write_bytes(content)
    release.register_tool_hash("ffmpeg")
    with pytest.raises(paths.ReleasePathError, match="release-tool-architecture"):
        paths.bundled_tool_path("ffmpeg")


@pytest.mark.parametrize("failure", ["missing", "unlisted", "wrong-runtime"])
def test_missing_unlisted_or_misplaced_tool_does_not_fallback(release, failure):
    if failure == "missing":
        release.tool_path("node").unlink()
        error_code = "release-tool-missing"
    elif failure == "unlisted":
        relative_path = release.manifest["executables"]["node_executable"]
        release.manifest["files"] = [entry for entry in release.manifest["files"] if entry["path"] != relative_path]
        release.write_manifest()
        error_code = "release-tool-unlisted"
    else:
        release.manifest["executables"]["node_executable"] = release.manifest["executables"]["python_executable"]
        release.write_manifest()
        error_code = "release-tool-layout"
    with patch("shutil.which", side_effect=AssertionError("No system fallback")):
        with pytest.raises(paths.ReleasePathError, match=error_code):
            paths.bundled_tool_path("node")


def test_host_architecture_mismatch_is_rejected_before_using_tools(release, monkeypatch):
    monkeypatch.setattr(paths.platform, "machine", lambda: "unsupported-cpu")
    with pytest.raises(paths.ReleasePathError, match="release-host-architecture"):
        paths.bundled_tool_path("python")


def test_source_mode_never_claims_a_bundled_tool(sandbox):
    with pytest.raises(paths.ReleasePathError, match="release-required"):
        paths.bundled_tool_path("python")
    paths.assert_private_interpreter(executable=str(sandbox.working_directory / "developer python"))


def test_release_interpreter_assertion_accepts_only_registered_python(release, monkeypatch):
    private_python = release.tool_path("python")
    paths.assert_private_interpreter(executable=str(private_python))
    interpreter_alias = release.working_directory / "python alias"
    interpreter_alias.symlink_to(private_python)
    paths.assert_private_interpreter(executable=str(interpreter_alias))
    monkeypatch.setattr(paths.sys, "executable", str(private_python))
    paths.assert_private_interpreter()
    with pytest.raises(paths.ReleasePathError, match="release-python-required"):
        paths.assert_private_interpreter(executable=str(release.tool_path("node")))


def test_release_subprocess_environment_isolated_without_changing_parent_or_path(release, monkeypatch):
    monkeypatch.setattr(paths.sys, "executable", str(release.tool_path("python")))
    parent_environment = {
        "PATH": "unchanged host path", "HTTPS_PROXY": "unchanged proxy",
        "LLM_MODEL_ID": "existing-model", "PYTHONPATH": "outside",
        "PYTHONHOME": "outside", "PYTHONSTARTUP": "outside",
        "NODE_OPTIONS": "--require outside.js", "NODE_PATH": "outside",
        "DYLD_LIBRARY_PATH": "outside", "LD_PRELOAD": "outside",
        "VIRTUAL_ENV": "outside", "CONDA_PREFIX": "outside",
        "__PYVENV_LAUNCHER__": "outside", "PYTHONIOENCODING": "latin1",
    }
    original_environment = dict(parent_environment)
    isolated_environment = paths.python_subprocess_environment(parent_environment)
    assert parent_environment == original_environment
    assert isolated_environment == {
        "PATH": "unchanged host path", "HTTPS_PROXY": "unchanged proxy",
        "LLM_MODEL_ID": "existing-model", "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
    }


def test_release_subprocess_environment_refuses_external_interpreter(release, monkeypatch):
    monkeypatch.setattr(paths.sys, "executable", str(release.working_directory / "system python"))
    with pytest.raises(paths.ReleasePathError, match="release-python-required"):
        paths.python_subprocess_environment({"PATH": "unchanged"})


def test_source_subprocess_environment_retains_developer_environment(sandbox):
    parent_environment = {"PYTHONPATH": "developer libraries", "VIRTUAL_ENV": "developer venv"}
    assert paths.python_subprocess_environment(parent_environment) == {
        **parent_environment, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"
    }


def test_missing_history_registry_returns_empty_without_discovery(release):
    legacy_exports = release.working_directory / "exports"
    legacy_exports.mkdir()
    (legacy_exports / "sent_intro_audit.json").write_text("[]")
    assert paths.historical_export_dirs() == ()
    assert not release.user_root.exists()


def test_registered_history_is_explicit_and_deduplicated(release):
    first_directory = release.working_directory / "\u5386\u53f2 exports"
    second_directory = release.working_directory / "other exports"
    first_directory.mkdir()
    second_directory.mkdir()
    registry_directory = release.user_root / "config"
    registry_directory.mkdir(parents=True)
    (registry_directory / "historical-exports.json").write_text(json.dumps({
        "schema_version": 1,
        "directories": [str(first_directory), str(second_directory), str(first_directory)],
    }), encoding="utf-8")
    assert paths.historical_export_dirs() == (first_directory, second_directory)


@pytest.mark.parametrize("registry_kind", ["missing-directory", "relative-directory", "malformed-json", "wrong-schema"])
def test_invalid_registered_history_fails_closed_instead_of_becoming_empty(release, registry_kind):
    registry_directory = release.user_root / "config"
    registry_directory.mkdir(parents=True)
    registry = {"schema_version": 1, "directories": []}
    if registry_kind == "missing-directory":
        registry["directories"] = [str(release.working_directory / "lost exports")]
    elif registry_kind == "relative-directory":
        registry["directories"] = ["exports"]
    elif registry_kind == "wrong-schema":
        registry["schema_version"] = 2
    registry_text = "invalid json" if registry_kind == "malformed-json" else json.dumps(registry)
    (registry_directory / "historical-exports.json").write_text(registry_text)
    with pytest.raises(paths.ReleasePathError, match="release-history-invalid"):
        paths.historical_export_dirs()


def test_development_config_and_cache_defaults_remain_source_relative(sandbox, config_module):
    config_path = sandbox.resource_root / "config.toml"
    config_path.write_text('[stores]\ndefault_store_id = "source-store"\n')
    assert config_module.resolve_config_path() == config_path
    assert config_module.load_store_settings()["default_store_id"] == "source-store"
    settings = config_module.load_content_review_settings()
    assert Path(settings["cache_dir"]) == sandbox.resource_root / "exports/content_review_cache"


def test_release_config_and_cache_defaults_ignore_baked_settings(release, config_module, monkeypatch):
    (release.resource_root / "config.toml").write_text('[stores]\ndefault_store_id = "baked-store"\n')
    (release.resource_root / ".env").write_text("CONTENT_REVIEW_CACHE_DIR=baked-cache\n")
    assert config_module.resolve_config_path() is None
    assert config_module.load_raw_config() == {}
    assert config_module.load_dotenv() is None
    assert "CONTENT_REVIEW_CACHE_DIR" not in os.environ
    assert Path(config_module.load_content_review_settings()["cache_dir"]) == release.user_root / "exports/content_review_cache"
    config_directory = release.user_root / "config"
    config_directory.mkdir(parents=True)
    config_path = config_directory / "config.toml"
    config_path.write_text('[stores]\ndefault_store_id = "user-store"\n')
    environment_path = config_directory / ".env"
    environment_path.write_text("CONTENT_REVIEW_CACHE_DIR=user-cache\n")
    assert config_module.resolve_config_path() == config_path
    assert config_module.load_store_settings()["default_store_id"] == "user-store"
    assert Path(config_module.load_content_review_settings()["cache_dir"]) == release.user_root / "user-cache"
    # load_dotenv is intentionally the mutating API; content settings above are not.
    monkeypatch.delenv("CONTENT_REVIEW_CACHE_DIR", raising=False)
    assert config_module.load_dotenv() == environment_path
    monkeypatch.delenv("CONTENT_REVIEW_CACHE_DIR", raising=False)


def test_release_explicit_config_precedes_environment_and_user_default(release, config_module, monkeypatch):
    config_directory = release.user_root / "config"
    config_directory.mkdir(parents=True)
    (config_directory / "config.toml").write_text('[stores]\ndefault_store_id = "default-store"\n')
    environment_config = release.working_directory / "environment config.toml"
    environment_config.write_text('[stores]\ndefault_store_id = "environment-store"\n')
    explicit_config = release.working_directory / "explicit config.toml"
    explicit_config.write_text('[stores]\ndefault_store_id = "explicit-store"\n')
    monkeypatch.setenv("ZN_SAMPLE_CONFIG", str(environment_config))
    assert config_module.resolve_config_path() == environment_config
    assert config_module.load_store_settings()["default_store_id"] == "environment-store"
    assert config_module.resolve_config_path(explicit_config) == explicit_config
    assert config_module.load_store_settings(config_path=explicit_config)["default_store_id"] == "explicit-store"
    monkeypatch.setenv("ZN_SAMPLE_CONFIG", str(release.working_directory / "missing.toml"))
    with pytest.raises(config_module.AppConfigError):
        config_module.resolve_config_path()


def test_release_content_cache_configuration_is_user_relative_and_preserves_precedence(release, config_module, monkeypatch):
    config_directory = release.user_root / "config"
    config_directory.mkdir(parents=True)
    config_path = config_directory / "config.toml"
    config_path.write_text('[content_review]\ncache_dir = "section cache"\n')
    environment_path = config_directory / ".env"
    environment_path.write_text("CONTENT_REVIEW_CACHE_DIR=dotenv cache\n")
    monkeypatch.setenv("CONTENT_REVIEW_CACHE_DIR", "process cache")
    monkeypatch.setenv("LLM_MODEL_ID", "existing-model")
    original_environment = dict(os.environ)
    assert Path(config_module.load_content_review_settings()["cache_dir"]) == release.user_root / "section cache"
    config_path.write_text("[content_review]\n")
    config_module.clear_config_cache()
    assert Path(config_module.load_content_review_settings()["cache_dir"]) == release.user_root / "dotenv cache"
    environment_path.unlink()
    assert Path(config_module.load_content_review_settings()["cache_dir"]) == release.user_root / "process cache"
    assert dict(os.environ) == original_environment


def test_release_content_settings_reject_cache_pointing_inside_bundle(release, config_module):
    config_directory = release.user_root / "config"
    config_directory.mkdir(parents=True)
    (config_directory / "config.toml").write_text(
        '[content_review]\ncache_dir = ' + json.dumps(str(release.resource_root / "cache")) + "\n"
    )
    with pytest.raises(paths.ReleasePathError, match="release-cache-readonly"):
        config_module.load_content_review_settings()
