"""Archive regressions with synthetic payloads, never native tools or user state."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from setup.release import build_bundle, package_windows_bundle, resources, runtime_artifacts


SOURCE_REVISION = "a" * 40


@pytest.fixture
def windows_bundle(tmp_path: Path) -> Path:
    bundle_root = tmp_path / "input with spaces" / "bundle"
    for relative_path, content in {
        "Start.cmd": b"fixture launcher; never executed\r\n",
        "app/.env.example": b"LLM_API_KEY=\n",
        "app/config.toml.example": b'[stores]\ndefault_store_id = ""\n',
        "runtime/python/python.exe": b"synthetic runtime; never executed",
    }.items():
        destination = bundle_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    manifest = build_bundle.create_manifest(
        bundle_root, "windows-x64", runtime_artifacts.get_target("windows-x64"), {},
        scope=resources.APPLICATION_SCOPE,
    )
    build_bundle.write_json(bundle_root / "release-manifest.json", manifest)
    return bundle_root


def test_zip_roundtrip_includes_dotfiles_and_exact_manifest_payload(windows_bundle: Path, tmp_path: Path):
    original_bytes = {
        path.relative_to(windows_bundle).as_posix(): path.read_bytes()
        for path in windows_bundle.rglob("*") if path.is_file()
    }
    output_directory = tmp_path / "release"
    report = package_windows_bundle.package_windows_bundle(
        windows_bundle, output_directory, SOURCE_REVISION,
    )
    archive_path = output_directory / report["archive"]
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {"bundle/" + name for name in original_bytes}
        for relative_path, expected_content in original_bytes.items():
            assert archive.read("bundle/" + relative_path) == expected_content
        archive.extractall(tmp_path / "extracted")
    assert build_bundle.verify_manifest(tmp_path / "extracted/bundle", "windows-x64")
    assert report["archive_sha256"] == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert report["source_revision"] == SOURCE_REVISION
    assert report["distribution"] == "internal-test"
    assert report["windows_desktop_acceptance"] == "required"
    assert json.loads((output_directory / "release-info.json").read_text()) == report
    assert (output_directory / (report["archive"] + ".sha256")).read_text() == (
        f"{report['archive_sha256']}  {report['archive']}\n"
    )
    assert original_bytes == {
        path.relative_to(windows_bundle).as_posix(): path.read_bytes()
        for path in windows_bundle.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("damage", ["changed-file", "unlisted-secret", "wrong-target", "runtime-only", "unsafe-version"])
def test_invalid_bundle_does_not_publish_archive(windows_bundle: Path, tmp_path: Path, damage: str):
    if damage == "changed-file":
        (windows_bundle / "Start.cmd").write_bytes(b"changed")
    elif damage == "unlisted-secret":
        (windows_bundle / "app/.env").write_bytes(b"synthetic-secret")
    else:
        manifest_path = windows_bundle / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if damage == "wrong-target":
            manifest["target"] = "macos-arm64"
        elif damage == "runtime-only":
            manifest["scope"] = "runtime-proof-not-full-application"
        else:
            manifest["application_version"] = "../escape"
        build_bundle.write_json(manifest_path, manifest)
    output_directory = tmp_path / "release"
    with pytest.raises(runtime_artifacts.RuntimeBlocked):
        package_windows_bundle.package_windows_bundle(windows_bundle, output_directory, SOURCE_REVISION)
    assert not output_directory.exists()


def test_existing_deliverable_is_not_overwritten(windows_bundle: Path, tmp_path: Path):
    output_directory = tmp_path / "release"
    report = package_windows_bundle.package_windows_bundle(windows_bundle, output_directory, SOURCE_REVISION)
    archive_path = output_directory / report["archive"]
    original_digest = runtime_artifacts.hash_file(archive_path)
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="archive-output-exists"):
        package_windows_bundle.package_windows_bundle(windows_bundle, output_directory, "b" * 40)
    assert runtime_artifacts.hash_file(archive_path) == original_digest


def test_output_cannot_pollute_input_bundle(windows_bundle: Path):
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="archive-output-inside-bundle"):
        package_windows_bundle.package_windows_bundle(windows_bundle, windows_bundle / "release", SOURCE_REVISION)
    assert not (windows_bundle / "release").exists()


def test_post_compression_byte_drift_is_detected(windows_bundle: Path, tmp_path: Path):
    manifest = build_bundle.verify_manifest(windows_bundle, "windows-x64")
    archive_path = tmp_path / "damaged.zip"
    with zipfile.ZipFile(archive_path, "x") as archive:
        for relative_path in [entry["path"] for entry in manifest["files"]] + ["release-manifest.json"]:
            content = (windows_bundle / relative_path).read_bytes()
            archive.writestr("bundle/" + relative_path, b"changed" if relative_path == "Start.cmd" else content)
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="zip-file-hash-mismatch"):
        package_windows_bundle.verify_archive(archive_path, windows_bundle, manifest)
