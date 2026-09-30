"""Offline regressions for distribution boundaries; never contact real accounts."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import struct
import sys
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

RELEASE_DIRECTORY = Path(__file__).resolve().parents[2] / "setup/release"
sys.path.insert(0, str(RELEASE_DIRECTORY))
try:
    import build_bundle
    import runtime_artifacts as artifacts
finally:
    sys.path.pop(0)


def make_artifact(content: bytes) -> dict:
    return {
        "version": "1.0.0",
        "url": "https://example.invalid/runtime.tar.gz",
        "sha256": hashlib.sha256(content).hexdigest(),
        "checksum_source": "https://example.invalid/checksums",
        "license_source": "https://example.invalid/license",
    }


def write_tar(path: Path, entries: list[tuple[str, bytes | str, bytes]]) -> None:
    with tarfile.open(path, "w") as archive:
        for name, content, member_type in entries:
            member = tarfile.TarInfo(name)
            member.type = member_type
            if member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = str(content)
                archive.addfile(member)
            else:
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))


def make_external_cli(package_root: Path, *, version: str | None = None) -> Path:
    runner = package_root / "scripts/run.js"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        "spawnSync(binaryPath, process.argv.slice(2), {stdio: 'inherit'});",
        encoding="utf-8",
    )
    metadata = {**artifacts.policy.RELEASE_EXTERNAL_CLI}
    if version is not None:
        metadata["version"] = version
    (package_root / "package.json").write_text(json.dumps(metadata), encoding="utf-8")
    (package_root / "bin").mkdir()
    (package_root / "bin/ziniao-cli").write_bytes(b"fake native executable")
    return runner


def test_policy_limits_targets_and_has_trusted_sources():
    assert set(artifacts.policy.RELEASE_TARGETS) == {"macos-arm64", "windows-x64"}
    for target_name in artifacts.policy.RELEASE_TARGETS:
        target = artifacts.get_target(target_name)
        assert target["python"]["version"] == artifacts.policy.RELEASE_PYTHON_VERSION
        assert target["node"]["version"] == artifacts.policy.RELEASE_NODE_VERSION
    with pytest.raises(artifacts.RuntimeBlocked, match="unsupported-target"):
        artifacts.get_target("macos-x64")


def test_python_and_vite_policy_cannot_drift():
    with patch.object(artifacts.policy, "RELEASE_PYTHON_VERSION", "3.13.0"):
        with pytest.raises(artifacts.RuntimeBlocked, match="python-policy"):
            artifacts.get_target("macos-arm64")
    with patch.object(artifacts.policy, "RELEASE_NODE_VERSION", "20.18.0"):
        with pytest.raises(artifacts.RuntimeBlocked, match="node-engine"):
            artifacts.get_target("macos-arm64")


def test_ffmpeg_lgpl_wrapped_output_is_verified_without_accepting_gpl_or_nonfree():
    wrapped_license = (
        "modify it under the terms of the GNU Lesser General Public\nLicense"
    )
    build_bundle.verify_ffmpeg_license(
        wrapped_license, "--disable-gpl --disable-nonfree"
    )
    for forbidden_option in ("--enable-gpl", "--enable-nonfree", "--enable-version3"):
        with pytest.raises(artifacts.RuntimeBlocked, match="ffmpeg-license-policy"):
            build_bundle.verify_ffmpeg_license(wrapped_license, forbidden_option)
    with pytest.raises(artifacts.RuntimeBlocked, match="ffmpeg-license-policy"):
        build_bundle.verify_ffmpeg_license("GNU General Public License", "")


def test_missing_hash_is_rejected_before_downloading(tmp_path):
    artifact = make_artifact(b"good")
    artifact["sha256"] = ""
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("network must not run")
    ):
        with pytest.raises(artifacts.RuntimeBlocked, match="trusted-sha256"):
            artifacts.download_verified(artifact, tmp_path / "runtime.tar.gz")


def test_corrupted_download_is_not_published(tmp_path):
    destination = tmp_path / "runtime.tar.gz"
    with pytest.raises(artifacts.RuntimeBlocked, match="download-checksum"):
        artifacts.download_verified(
            make_artifact(b"trusted"),
            destination,
            opener=lambda *arguments, **keywords: io.BytesIO(b"corrupted"),
        )
    assert list(tmp_path.iterdir()) == []


def test_verified_download_cache_is_rechecked(tmp_path):
    destination = tmp_path / "runtime.tar.gz"
    content = b"trusted"
    artifacts.download_verified(
        make_artifact(content),
        destination,
        opener=lambda *arguments, **keywords: io.BytesIO(content),
    )
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("cache should be used")
    ):
        assert (
            artifacts.download_verified(make_artifact(content), destination)
            == destination
        )
    destination.write_bytes(b"tampered")
    with pytest.raises(artifacts.RuntimeBlocked, match="cached-artifact-checksum"):
        artifacts.download_verified(make_artifact(content), destination)


@pytest.mark.parametrize(
    "member_name",
    ["../escape", "/escape", "C:/escape", "safe/../../escape", "safe\\escape"],
)
def test_tar_path_escape_is_rejected(tmp_path, member_name):
    archive = tmp_path / "runtime.tar"
    write_tar(archive, [(member_name, b"data", tarfile.REGTYPE)])
    with pytest.raises(artifacts.RuntimeBlocked, match="archive-path-escape"):
        artifacts.extract_verified_archive(archive, tmp_path / "extracted")
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("link_target", ["../../outside", "/outside", "C:/outside"])
def test_tar_symlink_escape_is_rejected(tmp_path, link_target):
    archive = tmp_path / "runtime.tar"
    write_tar(archive, [("python/link", link_target, tarfile.SYMTYPE)])
    with pytest.raises(artifacts.RuntimeBlocked, match="symlink-escape"):
        artifacts.extract_verified_archive(archive, tmp_path / "extracted")


def test_symlink_pivot_and_hardlinks_are_rejected(tmp_path):
    archive = tmp_path / "runtime.tar"
    write_tar(
        archive,
        [
            ("python/link", "real", tarfile.SYMTYPE),
            ("python/link/payload", b"data", tarfile.REGTYPE),
        ],
    )
    with pytest.raises(artifacts.RuntimeBlocked, match="through-symlink"):
        artifacts.extract_verified_archive(archive, tmp_path / "extracted")
    write_tar(archive, [("python/link", "../../outside", tarfile.LNKTYPE)])
    with pytest.raises(artifacts.RuntimeBlocked, match="special-file-or-hardlink"):
        artifacts.extract_verified_archive(archive, tmp_path / "extracted")


def test_safe_relative_python_interpreter_symlink_survives(tmp_path):
    archive = tmp_path / "runtime.tar"
    write_tar(
        archive,
        [
            ("python/bin/python3.12", b"native executable", tarfile.REGTYPE),
            ("python/bin/python3", "python3.12", tarfile.SYMTYPE),
        ],
    )
    destination = tmp_path / "extracted"
    artifacts.extract_verified_archive(archive, destination)
    assert (destination / "python/bin/python3").read_bytes() == b"native executable"


def test_zip_escape_and_unix_symlink_are_rejected(tmp_path):
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside", "data")
    with pytest.raises(artifacts.RuntimeBlocked, match="archive-path-escape"):
        artifacts.extract_verified_archive(archive_path, tmp_path / "extracted")
    with zipfile.ZipFile(archive_path, "w") as archive:
        link = zipfile.ZipInfo("link")
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "../../outside")
    with pytest.raises(artifacts.RuntimeBlocked, match="special-file-or-symlink"):
        artifacts.extract_verified_archive(archive_path, tmp_path / "extracted")


def test_windows_cannot_be_verified_on_mac():
    with (
        patch.object(artifacts.platform, "system", return_value="Darwin"),
        patch.object(artifacts.platform, "machine", return_value="arm64"),
    ):
        with pytest.raises(artifacts.RuntimeBlocked, match="native-host-required"):
            artifacts.require_native_host(artifacts.get_target("windows-x64"))


def test_environment_isolation_does_not_change_path_proxy_or_parent():
    parent = {
        "PATH": "original-path",
        "HTTPS_PROXY": "proxy-policy",
        "NODE_OPTIONS": "--require external.js",
        "NODE_PATH": "external",
        "PYTHONPATH": "external",
        "PYTHONHOME": "external",
        "VIRTUAL_ENV": "development",
        "DYLD_LIBRARY_PATH": "external",
    }
    original = dict(parent)
    isolated = artifacts.isolated_environment(parent)
    assert parent == original
    assert isolated["PATH"] == parent["PATH"]
    assert isolated["HTTPS_PROXY"] == parent["HTTPS_PROXY"]
    assert isolated["PYTHONNOUSERSITE"] == "1"
    assert not set(isolated).intersection(
        {
            "NODE_OPTIONS",
            "NODE_PATH",
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "DYLD_LIBRARY_PATH",
        }
    )


def test_external_cli_never_discovers_global_tools_or_copies_package(
    tmp_path, monkeypatch
):
    runner = make_external_cli(tmp_path / "technician-installed")
    monkeypatch.setattr(
        artifacts.policy,
        "RELEASE_EXTERNAL_CLI_RUNNER_SHA256",
        artifacts.hash_file(runner),
    )
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    with patch(
        "shutil.which", side_effect=AssertionError("global discovery forbidden")
    ):
        external = artifacts.resolve_external_cli(
            runner, bundle_root, artifacts.get_target("macos-arm64")
        )
    assert external["runner"] == runner
    assert list(bundle_root.iterdir()) == []
    assert (
        artifacts.policy.RELEASE_EXTERNAL_CLI["version"]
        == artifacts.policy.PINNED_ZINIAO_CLI
    )


def test_external_cli_wrong_version_and_embedded_package_are_rejected(tmp_path):
    runner = make_external_cli(tmp_path / "external", version="9.9.9")
    with pytest.raises(artifacts.RuntimeBlocked, match="fixed-version"):
        artifacts.resolve_external_cli(
            runner, tmp_path / "bundle", artifacts.get_target("macos-arm64")
        )
    runner = make_external_cli(tmp_path / "bundle/runtime/ziniao")
    with pytest.raises(artifacts.RuntimeBlocked, match="must-not-be-bundled"):
        artifacts.resolve_external_cli(
            runner, tmp_path / "bundle", artifacts.get_target("macos-arm64")
        )


def test_external_cli_bare_node_chain_is_a_stop_condition(tmp_path):
    runner = make_external_cli(tmp_path / "external")
    runner.write_text(runner.read_text() + "\nspawnSync('node', []);", encoding="utf-8")
    with pytest.raises(artifacts.RuntimeBlocked, match="global-node-chain"):
        artifacts.resolve_external_cli(
            runner, tmp_path / "bundle", artifacts.get_target("macos-arm64")
        )


def test_external_cli_unreviewed_wrapper_change_is_rejected(tmp_path, monkeypatch):
    runner = make_external_cli(tmp_path / "external")
    monkeypatch.setattr(
        artifacts.policy,
        "RELEASE_EXTERNAL_CLI_RUNNER_SHA256",
        artifacts.hash_file(runner),
    )
    runner.write_text(
        runner.read_text() + "\nconsole.log('changed');", encoding="utf-8"
    )
    with pytest.raises(artifacts.RuntimeBlocked, match="wrapper-drift"):
        artifacts.resolve_external_cli(
            runner, tmp_path / "bundle", artifacts.get_target("macos-arm64")
        )


def test_binary_architecture_cannot_be_inferred_from_filename(tmp_path):
    executable = tmp_path / "python3"
    executable.write_bytes(struct.pack("<8I", 0xFEEDFACF, 0x01000007, 0, 2, 0, 0, 0, 0))
    with pytest.raises(artifacts.RuntimeBlocked, match="native-arm64"):
        artifacts.validate_binary_architecture(
            executable, artifacts.get_target("macos-arm64")
        )
    executable.write_bytes(struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0))
    artifacts.validate_binary_architecture(
        executable, artifacts.get_target("macos-arm64")
    )


def write_macho(path: Path, library: str) -> None:
    library_bytes = library.encode() + b"\0"
    command_size = (24 + len(library_bytes) + 7) // 8 * 8
    command = struct.pack("<6I", 0xC, command_size, 24, 0, 0, 0) + library_bytes
    command = command.ljust(command_size, b"\0")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 1, len(command), 0, 0)
        + command
    )


@pytest.mark.parametrize("fat64", [False, True])
def test_universal_macho_audits_arm64_slice_and_rejects_invalid_slice_table(
    tmp_path, fat64
):
    executable = tmp_path / "universal.so"
    payload = bytearray(192)
    struct.pack_into(">II", payload, 0, 0xCAFEBABF if fat64 else 0xCAFEBABE, 2)
    record_format = ">IIQQII" if fat64 else ">IIIII"
    record_size = struct.calcsize(record_format)
    for index, cpu_type in enumerate((0x01000007, 0x0100000C)):
        slice_offset = 128 + index * 32
        record = (
            (cpu_type, 0, slice_offset, 32, 5, 0)
            if fat64
            else (cpu_type, 0, slice_offset, 32, 5)
        )
        struct.pack_into(record_format, payload, 8 + index * record_size, *record)
        struct.pack_into(
            "<8I", payload, slice_offset, 0xFEEDFACF, cpu_type, 0, 2, 0, 0, 0, 0
        )
    executable.write_bytes(payload)
    assert artifacts.inspect_macho(executable)["cpu_type"] == 0x0100000C
    artifacts.validate_binary_architecture(
        executable, artifacts.get_target("macos-arm64")
    )
    struct.pack_into(">Q" if fat64 else ">I", payload, 8 + record_size + 8, 9999)
    executable.write_bytes(payload)
    with pytest.raises(artifacts.RuntimeBlocked, match="slice-bounds"):
        artifacts.inspect_macho(executable)
    struct.pack_into(">I", payload, 8 + record_size, 0x01000007)
    executable.write_bytes(payload)
    with pytest.raises(artifacts.RuntimeBlocked, match="requires-one-arm64-slice"):
        artifacts.inspect_macho(executable)


def test_macos_dependency_audit_rejects_developer_library_paths(tmp_path):
    bundle_root = tmp_path / "bundle"
    target = artifacts.get_target("macos-arm64")
    for field in ("python_executable", "node_executable", "ffmpeg_executable"):
        write_macho(bundle_root / target[field], "/usr/lib/libSystem.B.dylib")
    assert artifacts.audit_macos_dependencies(bundle_root)["dependency_count"] == 3
    developer_library = tmp_path / "global/libcustom.dylib"
    developer_library.parent.mkdir()
    developer_library.write_bytes(b"developer library")
    write_macho(bundle_root / target["ffmpeg_executable"], str(developer_library))
    with pytest.raises(artifacts.RuntimeBlocked, match="non-bundled-mach-o-dependency"):
        artifacts.audit_macos_dependencies(bundle_root)


def write_pe(path: Path, library: str, *, delayed_library: str | None = None) -> None:
    payload = bytearray(1024)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 60, 128)
    payload[128:132] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", payload, 132, 0x8664, 1, 0, 0, 0, 240, 0)
    optional_offset = 152
    struct.pack_into("<H", payload, optional_offset, 0x20B)
    struct.pack_into("<II", payload, optional_offset + 112 + 8, 0x1000, 40)
    struct.pack_into("<IIII", payload, optional_offset + 240 + 8, 512, 0x1000, 512, 512)
    struct.pack_into("<I", payload, 512 + 12, 0x1040)
    library_bytes = library.encode() + b"\0"
    payload[576 : 576 + len(library_bytes)] = library_bytes
    if delayed_library:
        struct.pack_into("<II", payload, optional_offset + 112 + 13 * 8, 0x1080, 64)
        struct.pack_into("<II", payload, 640, 1, 0x10E0)
        library_bytes = delayed_library.encode() + b"\0"
        payload[736 : 736 + len(library_bytes)] = library_bytes
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_windows_pe_architecture_and_regular_and_delayed_imports(tmp_path):
    executable = tmp_path / "python.exe"
    write_pe(executable, "KERNEL32.dll", delayed_library="VCRUNTIME140.dll")
    artifacts.validate_binary_architecture(
        executable, artifacts.get_target("windows-x64")
    )
    assert artifacts.inspect_pe_imports(executable) == [
        "kernel32.dll",
        "vcruntime140.dll",
    ]


def test_windows_compiler_runtime_is_not_satisfied_by_global_installation(
    tmp_path, monkeypatch
):
    bundle_root = tmp_path / "bundle"
    system_root = tmp_path / "Windows/System32"
    system_root.mkdir(parents=True)
    monkeypatch.setenv("SystemRoot", str(system_root.parent))
    for library in ("kernel32.dll", "vcruntime140.dll"):
        (system_root / library).write_bytes(b"global DLL")
    target = artifacts.get_target("windows-x64")
    for field in ("python_executable", "node_executable", "ffmpeg_executable"):
        write_pe(bundle_root / target[field], "KERNEL32.dll")
    write_pe(bundle_root / target["python_executable"], "VCRUNTIME140.dll")
    with pytest.raises(artifacts.RuntimeBlocked, match="non-bundled-pe-dependency"):
        artifacts.audit_windows_dependencies(bundle_root)
    write_pe(bundle_root / "runtime/python/vcruntime140.dll", "KERNEL32.dll")
    assert artifacts.audit_windows_dependencies(bundle_root)["binary_count"] == 4


def test_manifest_detects_tampering_and_unlisted_files(tmp_path):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    payload = bundle_root / "runtime/python/payload"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"trusted")
    target = artifacts.get_target("macos-arm64")
    manifest = build_bundle.create_manifest(bundle_root, "macos-arm64", target, {})
    build_bundle.write_json(bundle_root / "release-manifest.json", manifest)
    build_bundle.verify_manifest(bundle_root, "macos-arm64")
    payload.write_bytes(b"tampered")
    with pytest.raises(artifacts.RuntimeBlocked, match="hash-mismatch"):
        build_bundle.verify_manifest(bundle_root, "macos-arm64")
    payload.write_bytes(b"trusted")
    (bundle_root / "undeclared").write_bytes(b"untrusted")
    with pytest.raises(artifacts.RuntimeBlocked, match="unmanifested"):
        build_bundle.verify_manifest(bundle_root, "macos-arm64")
    assert str(tmp_path) not in json.dumps(manifest)
    assert manifest["external_prerequisites"][0]["bundled"] is False


def test_upstream_pip_build_paths_are_removed_without_removing_production_metadata(
    tmp_path,
):
    target = artifacts.get_target("macos-arm64")
    site_packages = tmp_path / "lib/python3.12/site-packages"
    for package_name in (
        "pip",
        "pip-26.2.1.dist-info",
        "fastapi",
        "fastapi-0.141.1.dist-info",
    ):
        (site_packages / package_name).mkdir(parents=True)
    (site_packages / "pip-26.2.1.dist-info/direct_url.json").write_text(
        '{"url":"file:///upstream/build/pip.whl"}', encoding="utf-8"
    )
    build_bundle.remove_upstream_package_manager(tmp_path, target)
    assert not (site_packages / "pip").exists()
    assert not (site_packages / "pip-26.2.1.dist-info").exists()
    assert (site_packages / "fastapi").is_dir()
    assert (site_packages / "fastapi-0.141.1.dist-info").is_dir()


def test_verification_never_promotes_relocation_to_clean_machine_acceptance(tmp_path):
    arguments = argparse.Namespace(
        command="verify-runtime", target="macos-arm64", ziniao_runner=None
    )
    with (
        patch.object(artifacts, "require_native_host"),
        patch.object(build_bundle, "ROOT", tmp_path),
        patch.object(
            build_bundle, "verify_manifest", return_value={"target": "macos-arm64"}
        ),
        patch.object(build_bundle, "probe_runtime", return_value={"synthetic": True}),
        patch.object(build_bundle.shutil, "copytree"),
        patch(
            "shutil.which",
            side_effect=AssertionError("runtime must not search globals"),
        ),
    ):
        report, exit_code = build_bundle.execute(arguments)
    assert exit_code == 2
    assert report["status"] == "BLOCKED"
    assert report["native_verified"] is True
    assert report["relocation_verified"] is True
    assert report["clean_environment_verified"] is False
    assert report["parent_environment_unchanged"] is True


def test_all_probe_subprocesses_use_absolute_private_tools_without_global_discovery(
    tmp_path, monkeypatch
):
    import subprocess

    bundle_root = tmp_path / "bundle"
    working_directory = tmp_path / "unrelated working directory"
    working_directory.mkdir()
    runner_path = make_external_cli(tmp_path / "external")
    monkeypatch.setattr(
        artifacts.policy,
        "RELEASE_EXTERNAL_CLI_RUNNER_SHA256",
        artifacts.hash_file(runner_path),
    )
    target = artifacts.get_target("macos-arm64")

    def simulate_runtime(command, **keywords):
        stdout = b""
        if "-c" in command:
            stdout = b'{"version":"3.12.14"}'
        elif command[1:] == ["--version"]:
            stdout = ("v" + artifacts.policy.RELEASE_NODE_VERSION).encode()
        elif command[-1] == "--version":
            stdout = (
                "ziniao-cli version " + artifacts.policy.PINNED_ZINIAO_CLI
            ).encode()
        elif command[-1] == "-version":
            stdout = (
                "ffmpeg version " + artifacts.policy.RELEASE_FFMPEG_VERSION + " "
            ).encode()
        elif command[-1] == "-L":
            stdout = b"GNU Lesser General Public\nLicense"
        elif command[-1] == "pipe:1":
            stdout = b"\xff\xd8test-jpeg\xff\xd9"
        return subprocess.CompletedProcess(command, 0, stdout, b"")

    with (
        patch.object(build_bundle, "verify_manifest"),
        patch.object(artifacts, "validate_binary_architecture"),
        patch.object(artifacts, "audit_macos_dependencies", return_value={}),
        patch.object(
            build_bundle.subprocess, "run", side_effect=simulate_runtime
        ) as child_runner,
        patch(
            "shutil.which",
            side_effect=AssertionError("runtime cannot use global tools"),
        ),
    ):
        result = build_bundle.probe_runtime(
            bundle_root, "macos-arm64", target, runner_path, working_directory
        )
    assert result["external_cli"]["bundled"] is False
    for call in child_runner.call_args_list:
        command = call.args[0]
        assert Path(command[0]).is_absolute()
        assert Path(command[0]).is_relative_to(bundle_root)
        assert call.kwargs["shell"] is False
        assert call.kwargs["env"]["PATH"] == os.environ["PATH"]
    assert child_runner.call_args_list[-1].args[0] == [
        str(bundle_root / target["node_executable"]),
        runner_path.as_posix(),
        "--version",
    ]


def test_subprocess_failures_never_echo_configuration_values(tmp_path):
    import subprocess

    completed = subprocess.CompletedProcess(
        ["/private/node"], 1, b"secret-value", b"apiKey=secret-value"
    )
    with patch.object(build_bundle.subprocess, "run", return_value=completed) as runner:
        with pytest.raises(artifacts.RuntimeBlocked, match="probe-exit-1") as caught:
            build_bundle.run_checked(
                ["/private/node", "--version"], cwd=tmp_path, label="probe"
            )
    assert "secret-value" not in str(caught.value)
    assert runner.call_args.kwargs["shell"] is False
    assert runner.call_args.kwargs["env"]["PATH"] == os.environ["PATH"]
