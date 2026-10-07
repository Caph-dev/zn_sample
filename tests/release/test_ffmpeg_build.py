"""Check native target selection using a synthetic compiler and source tree."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from setup.release import build_bundle, runtime_artifacts


@pytest.mark.parametrize(
    "target_name,expected_executable",
    [("windows-x64", "ffmpeg.exe"), ("macos-arm64", "ffmpeg")],
)
def test_make_target_matches_packaged_executable(
    tmp_path: Path, monkeypatch, target_name: str, expected_executable: str,
):
    staging_root = tmp_path / "payload"
    (staging_root / "runtime").mkdir(parents=True)
    archive_path = tmp_path / "verified-source.tar.xz"
    archive_path.write_bytes(b"synthetic source archive")
    executable_content = b"synthetic binary; never executed"

    def extract_synthetic_source(archive: Path, destination: Path) -> None:
        assert archive == archive_path
        source_directory = destination / build_bundle.policy.RELEASE_FFMPEG_SOURCE["archive_root"]
        source_directory.mkdir(parents=True)
        for license_name in ("COPYING.LGPLv2.1", "LICENSE.md"):
            (source_directory / license_name).write_text("synthetic license\n", encoding="utf-8")

    commands = []

    def run_synthetic_compiler(command: list[str], **options) -> None:
        commands.append((command, options["label"]))
        if options["label"] == "ffmpeg-compile":
            assert command[-1] == expected_executable
            (options["cwd"] / expected_executable).write_bytes(executable_content)

    monkeypatch.setattr(build_bundle.artifacts, "download_verified", lambda *arguments: archive_path)
    monkeypatch.setattr(build_bundle.artifacts, "extract_verified_archive", extract_synthetic_source)
    monkeypatch.setattr(build_bundle, "resolve_build_tool", lambda explicit, default: explicit or default)
    monkeypatch.setattr(build_bundle, "run_checked", run_synthetic_compiler)
    arguments = argparse.Namespace(ffmpeg_shell="fixture-shell", ffmpeg_cc="fixture-cc", ffmpeg_make="fixture-make")
    target = runtime_artifacts.get_target(target_name)
    build_bundle.build_ffmpeg(staging_root, tmp_path / "cache", target, arguments)

    assert [label for command, label in commands] == ["ffmpeg-configure", "ffmpeg-compile"]
    assert (staging_root / target["ffmpeg_executable"]).read_bytes() == executable_content
    other_executable = "ffmpeg" if expected_executable == "ffmpeg.exe" else "ffmpeg.exe"
    assert not (staging_root / "runtime/ffmpeg" / other_executable).exists()
    assert not (staging_root / "ffmpeg-build").exists()
    assert (staging_root / "sources" / archive_path.name).read_bytes() == archive_path.read_bytes()
