"""Offline release-tool contracts, using synthetic binaries and user registration.

Platform simulation covers resolution only, not native runtime acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from assistant import paths
from scripts.lib import creator_video_review, zclaw_cli


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDITED_TEST_WRAPPER = (
    "const {spawnSync} = require('child_process');\n"
    "const path = require('path');\n"
    "const binaryPath = path.join(__dirname, '..', 'bin', "
    "process.platform === 'win32' ? 'ziniao-cli.exe' : 'ziniao-cli');\n"
    "spawnSync(binaryPath, process.argv.slice(2), {stdio: 'inherit'});\n"
)


def make_native_image(target_name: str, *, wrong_architecture: bool = False) -> bytes:
    if target_name == "macos-arm64":
        cpu_type = 0x01000007 if wrong_architecture else 0x0100000C
        return struct.pack("<8I", 0xFEEDFACF, cpu_type, 0, 2, 0, 0, 0, 0)
    image = bytearray(512)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 60, 128)
    image[128:132] = b"PE\0\0"
    machine_type = 0xAA64 if wrong_architecture else 0x8664
    struct.pack_into("<HHIIIHH", image, 132, machine_type, 0, 0, 0, 0, 240, 0)
    struct.pack_into("<H", image, 152, 0x20B)
    return bytes(image)


def write_json(destination: Path, payload: object) -> None:
    destination.write_text(json.dumps(payload), encoding="utf-8")


def hash_file(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()


@dataclass
class ReleaseFixture:
    target_name: str
    bundle_root: Path
    tools: dict[str, Path]
    runner: Path
    binary: Path
    registration_path: Path
    artifacts: ModuleType
    manifest: dict

    def save_manifest(self) -> None:
        write_json(self.bundle_root / "release-manifest.json", self.manifest)

    def refresh_tool_hash(self, tool_name: str) -> None:
        tool_path = self.tools[tool_name]
        relative_path = tool_path.relative_to(self.bundle_root).as_posix()
        for entry in self.manifest["files"]:
            if entry["path"] == relative_path:
                entry["sha256"] = hash_file(tool_path)
        self.save_manifest()

    def save_registration(self, **changes: object) -> None:
        registration = {
            "schema_version": 1,
            "runner_path": str(self.runner),
            "binary_sha256": hash_file(self.binary),
        }
        registration.update(changes)
        write_json(self.registration_path, registration)


@pytest.fixture(autouse=True)
def forbid_external_execution(monkeypatch):
    forbidden_calls = Mock(side_effect=AssertionError("tool lookup/execution/network forbidden"))
    monkeypatch.setattr(shutil, "which", forbidden_calls)
    monkeypatch.setattr(subprocess, "run", forbidden_calls)
    monkeypatch.setattr(subprocess, "Popen", forbidden_calls)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_calls)
    monkeypatch.setattr(socket.socket, "connect", forbidden_calls)
    yield forbidden_calls
    forbidden_calls.assert_not_called()


@pytest.fixture(params=["macos-arm64", "windows-x64"])
def release_fixture(request, tmp_path, monkeypatch):
    target_name = request.param
    bundle_root = tmp_path / "\u53d1\u5e03 package"
    resource_root = bundle_root / "app"
    helper_root = resource_root / "setup" / "release"
    helper_root.mkdir(parents=True)
    # Copy only policy code: never copy real CLI installations or customer config.
    shutil.copyfile(
        PROJECT_ROOT / "setup/release/runtime_artifacts.py",
        helper_root / "runtime_artifacts.py",
    )
    shutil.copyfile(PROJECT_ROOT / "setup/install_deps.py", resource_root / "setup/install_deps.py")
    user_root = tmp_path / "\u7528\u6237 data"
    configuration_root = user_root / "config"
    configuration_root.mkdir(parents=True)
    monkeypatch.setattr(paths, "application_resource_dir", lambda: resource_root)
    monkeypatch.setattr(paths, "user_data_dir", lambda: user_root)
    monkeypatch.setattr(zclaw_cli, "application_resource_dir", lambda: resource_root)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(sys, "platform", "darwin" if target_name == "macos-arm64" else "win32")
    monkeypatch.setattr(paths.platform, "machine", lambda: "arm64" if target_name == "macos-arm64" else "AMD64")
    unrelated_directory = tmp_path / "unrelated working directory"
    unrelated_directory.mkdir()
    monkeypatch.chdir(unrelated_directory)
    zclaw_cli._release_artifacts_module.cache_clear()
    try:
        artifacts = zclaw_cli._release_artifacts_module()
        target = artifacts.get_target(target_name)
        tools = {}
        manifest = {"schema_version": 1, "target": target_name, "executables": {}, "files": []}
        for tool_name in ("python", "node", "ffmpeg"):
            relative_path = target[f"{tool_name}_executable"]
            tool_path = bundle_root / relative_path
            tool_path.parent.mkdir(parents=True, exist_ok=True)
            tool_path.write_bytes(make_native_image(target_name))
            tools[tool_name] = tool_path
            manifest["executables"][f"{tool_name}_executable"] = relative_path
            manifest["files"].append({"path": relative_path, "sha256": hash_file(tool_path)})
        package_root = tmp_path / "technician installed" / "node_modules" / "@ziniao-open" / "cli"
        runner = package_root / "scripts" / "run.js"
        runner.parent.mkdir(parents=True)
        runner.write_text(AUDITED_TEST_WRAPPER, encoding="utf-8")
        write_json(package_root / "package.json", {"name": "@ziniao-open/cli", "version": "1.0.7"})
        binary = package_root / "bin" / ("ziniao-cli.exe" if target_name == "windows-x64" else "ziniao-cli")
        binary.parent.mkdir()
        binary.write_bytes(make_native_image(target_name))
        monkeypatch.setattr(artifacts.policy, "RELEASE_EXTERNAL_CLI_RUNNER_SHA256", hash_file(runner))
        fixture = ReleaseFixture(
            target_name, bundle_root, tools, runner, binary,
            configuration_root / "external-cli.json", artifacts, manifest,
        )
        fixture.save_manifest()
        fixture.save_registration()
        yield fixture
    finally:
        zclaw_cli._release_artifacts_module.cache_clear()


def test_release_cli_uses_private_node_and_registered_external_runner(release_fixture):
    original_path = os.environ.get("PATH")
    injected_lookup = Mock(side_effect=AssertionError("injected PATH lookup forbidden"))
    assert zclaw_cli.resolve_ziniao_cli_command(which=injected_lookup) == [
        release_fixture.tools["node"].as_posix(), release_fixture.runner.as_posix(),
    ]
    injected_lookup.assert_not_called()
    assert os.environ.get("PATH") == original_path
    assert not release_fixture.runner.is_relative_to(release_fixture.bundle_root)
    for tool_name, expected_path in release_fixture.tools.items():
        assert paths.bundled_tool_path(tool_name) == expected_path


@pytest.mark.parametrize("configured_path", [None, "", "bundled"])
def test_release_ffmpeg_uses_manifest_tool_without_path_lookup(release_fixture, configured_path):
    expected_path = release_fixture.tools["ffmpeg"]
    setting = expected_path.as_posix() if configured_path == "bundled" else configured_path
    assert creator_video_review._resolve_ffmpeg_executable({"ffmpeg_path": setting}) == expected_path.as_posix()


def test_release_ffmpeg_rejects_old_development_path(release_fixture, tmp_path):
    old_executable = tmp_path / "old developer tools" / "ffmpeg"
    old_executable.parent.mkdir()
    old_executable.write_bytes(make_native_image(release_fixture.target_name))
    with pytest.raises(paths.ReleasePathError, match="release-ffmpeg-config"):
        creator_video_review._resolve_ffmpeg_executable({"ffmpeg_path": str(old_executable)})


@pytest.mark.parametrize("tool_name", ["python", "node", "ffmpeg"])
@pytest.mark.parametrize("damage", ["missing", "modified", "unlisted", "architecture"])
def test_release_tool_damage_fails_without_fallback_or_execution(release_fixture, tool_name, damage):
    tool_path = release_fixture.tools[tool_name]
    expected_code = {
        "missing": "release-tool-missing", "modified": "release-tool-hash",
        "unlisted": "release-tool-unlisted", "architecture": "release-tool-architecture",
    }[damage]
    if damage == "missing":
        tool_path.unlink()
    elif damage == "modified":
        tool_path.write_bytes(tool_path.read_bytes() + b"modified")
    elif damage == "unlisted":
        release_fixture.manifest["files"] = [
            entry for entry in release_fixture.manifest["files"]
            if entry["path"] != tool_path.relative_to(release_fixture.bundle_root).as_posix()
        ]
        release_fixture.save_manifest()
    else:
        tool_path.write_bytes(make_native_image(release_fixture.target_name, wrong_architecture=True))
        release_fixture.refresh_tool_hash(tool_name)
    with pytest.raises(paths.ReleasePathError, match=expected_code):
        paths.bundled_tool_path(tool_name)
    if tool_name == "node":
        runner = Mock(side_effect=AssertionError("invalid tool must not execute"))
        with pytest.raises(paths.ReleasePathError, match=expected_code):
            zclaw_cli.run_ziniao_cli(["--version"], runner=runner)
        runner.assert_not_called()
    elif tool_name == "ffmpeg":
        with pytest.raises(paths.ReleasePathError, match=expected_code):
            creator_video_review._resolve_ffmpeg_executable({"ffmpeg_path": None})


def test_release_host_architecture_mismatch_rejects_tools(release_fixture, monkeypatch):
    monkeypatch.setattr(paths.platform, "machine", lambda: "unsupported-architecture")
    with pytest.raises(paths.ReleasePathError, match="release-host-architecture"):
        zclaw_cli.resolve_ziniao_cli_command()


@pytest.mark.parametrize("damage", [
    "missing", "invalid-json", "wrong-schema", "unknown-field", "missing-field", "wrong-type",
])
def test_release_cli_registration_errors_fail_closed(release_fixture, damage):
    registration_path = release_fixture.registration_path
    if damage == "missing":
        registration_path.unlink()
    elif damage == "invalid-json":
        registration_path.write_text("{broken", encoding="utf-8")
    else:
        registration = json.loads(registration_path.read_text(encoding="utf-8"))
        if damage == "wrong-schema":
            registration["schema_version"] = 2
        elif damage == "unknown-field":
            registration["command"] = "npx"
        elif damage == "missing-field":
            del registration["binary_sha256"]
        else:
            registration["runner_path"] = [str(release_fixture.runner)]
        write_json(registration_path, registration)
    with pytest.raises(paths.ReleasePathError, match="release-cli-registration"):
        zclaw_cli.run_ziniao_cli(["--version"])


@pytest.mark.parametrize("damage", [
    "package-name", "package-version", "missing-metadata", "wrapper-drift", "global-node-chain",
    "missing-runner", "missing-binary", "architecture", "relative-runner", "bundled-runner",
])
def test_release_external_cli_is_revalidated_against_real_policy(release_fixture, damage):
    package_root = release_fixture.runner.parent.parent
    if damage in {"package-name", "package-version"}:
        metadata = {"name": "@ziniao-open/cli", "version": "1.0.7"}
        metadata["name" if damage == "package-name" else "version"] = "unapproved"
        write_json(package_root / "package.json", metadata)
    elif damage == "missing-metadata":
        (package_root / "package.json").unlink()
    elif damage in {"wrapper-drift", "global-node-chain"}:
        addition = "\nconsole.log('changed');" if damage == "wrapper-drift" else "\nspawnSync('node', []);"
        release_fixture.runner.write_text(AUDITED_TEST_WRAPPER + addition, encoding="utf-8")
    elif damage == "missing-runner":
        release_fixture.runner.unlink()
    elif damage == "missing-binary":
        release_fixture.binary.unlink()
    elif damage == "architecture":
        release_fixture.binary.write_bytes(make_native_image(release_fixture.target_name, wrong_architecture=True))
        release_fixture.save_registration()
    elif damage == "relative-runner":
        release_fixture.save_registration(runner_path="node_modules/@ziniao-open/cli/scripts/run.js")
    else:
        bundled_runner = release_fixture.bundle_root / "app" / "cli" / "scripts" / "run.js"
        bundled_runner.parent.mkdir(parents=True)
        bundled_runner.write_text(AUDITED_TEST_WRAPPER, encoding="utf-8")
        release_fixture.save_registration(runner_path=str(bundled_runner))
    with pytest.raises(paths.ReleasePathError, match="release-cli-invalid"):
        zclaw_cli.run_ziniao_cli(["--version"])


def test_release_external_cli_native_hash_change_requires_registration(release_fixture):
    release_fixture.binary.write_bytes(release_fixture.binary.read_bytes() + b"native binary changed")
    with pytest.raises(paths.ReleasePathError, match="release-cli-hash"):
        zclaw_cli.run_ziniao_cli(["--version"])


def test_release_subprocess_retains_utf8_no_shell_and_parent_path(release_fixture, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(release_fixture.tools["python"]))
    monkeypatch.setenv("NODE_OPTIONS", "--require developer-hook.js")
    monkeypatch.setenv("PYTHONPATH", "developer-site-packages")
    original_environment = dict(os.environ)
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, "fake version", ""))
    zclaw_cli.run_ziniao_cli(["--version"], timeout=17, runner=runner)
    runner.assert_called_once()
    arguments, options = runner.call_args
    assert arguments[0] == [release_fixture.tools["node"].as_posix(), release_fixture.runner.as_posix(), "--version"]
    assert options["encoding"] == "utf-8"
    assert options["errors"] == "replace"
    assert options["shell"] is False
    assert options["capture_output"] is True
    assert options["text"] is True
    assert options["timeout"] == 17
    assert options["env"].get("PATH") == original_environment.get("PATH")
    assert options["env"]["PYTHONUTF8"] == "1"
    assert options["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "NODE_OPTIONS" not in options["env"]
    assert "PYTHONPATH" not in options["env"]
    assert dict(os.environ) == original_environment


@pytest.mark.parametrize("windows_shim", [False, True])
def test_development_cli_keeps_injected_tool_resolution(tmp_path, monkeypatch, windows_shim):
    monkeypatch.setattr(paths, "application_resource_dir", lambda: tmp_path / "source")
    cli_path = tmp_path / ("ziniao-cli.cmd" if windows_shim else "ziniao-cli")
    cli_path.write_text("fake development launcher", encoding="utf-8")
    node_path = tmp_path / "node.exe"
    node_path.write_text("fake development node", encoding="utf-8")
    runner_path = tmp_path / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"
    runner_path.parent.mkdir(parents=True)
    runner_path.write_text("fake development wrapper", encoding="utf-8")
    available_tools = {cli_path.name: str(cli_path), "node": str(node_path)}
    lookup = Mock(side_effect=available_tools.get)
    expected_command = [node_path.as_posix(), runner_path.as_posix()] if windows_shim else [cli_path.as_posix()]
    assert zclaw_cli.resolve_ziniao_cli_command(which=lookup) == expected_command
    assert lookup.called


def test_development_ffmpeg_keeps_configuration_then_path_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "application_resource_dir", lambda: tmp_path / "source")
    lookup = Mock(return_value="/development/tools/ffmpeg")
    monkeypatch.setattr(creator_video_review.shutil, "which", lookup)
    assert creator_video_review._resolve_ffmpeg_executable({"ffmpeg_path": "/configured/ffmpeg"}) == "/configured/ffmpeg"
    lookup.assert_not_called()
    assert creator_video_review._resolve_ffmpeg_executable({"ffmpeg_path": None}) == "/development/tools/ffmpeg"
    lookup.assert_called_once_with("ffmpeg")
