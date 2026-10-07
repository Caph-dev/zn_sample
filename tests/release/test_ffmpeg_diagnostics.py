"""Offline compiler failure evidence; no real toolchain or business access."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from setup.release import build_bundle


@pytest.mark.parametrize("label", ["ffmpeg-configure", "ffmpeg-compile"])
@pytest.mark.parametrize("failure", ["exit", "timeout", "unavailable"])
def test_failure_logs_survive_source_cleanup_and_keep_original_error(
    tmp_path: Path, monkeypatch, label: str, failure: str,
):
    source_directory = tmp_path / "temporary build" / "ffmpeg"
    config_log = source_directory / "ffbuild/config.log"
    config_log.parent.mkdir(parents=True)
    config_log.write_text("check_cc: unsupported compiler option\nAPP_SECRET='synthetic-config-secret'\n")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-environment-secret")
    stdout = f"CC libavcodec/example.o\n{source_directory}/example.c\nsynthetic-environment-secret\n".encode()
    stderr = b"error: unknown type name 'fixture_type'\nAuthorization: Bearer synthetic-bearer\n"

    def fail_command(command, **options):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"], output=stdout, stderr=stderr)
        if failure == "unavailable":
            raise OSError("synthetic-environment-secret must not be logged")
        return subprocess.CompletedProcess(command, 2, stdout, stderr)

    monkeypatch.setattr(build_bundle.subprocess, "run", fail_command)
    diagnostics_directory = tmp_path / "release/ffmpeg-diagnostics"
    expected_error = f"{label}-exit-2" if failure == "exit" else f"{label}-unavailable-or-timeout"
    with pytest.raises(build_bundle.RuntimeBlocked, match=expected_error):
        build_bundle.run_checked(
            ["/synthetic/tool/make"], cwd=source_directory, label=label,
            ffmpeg_diagnostics_dir=diagnostics_directory,
        )
    config_log.unlink()
    assert len(list(diagnostics_directory.iterdir())) == 4
    output = "".join(path.read_text() for path in diagnostics_directory.iterdir())
    for secret in ("synthetic-config-secret", "synthetic-environment-secret", "synthetic-bearer"):
        assert secret not in output
    assert str(source_directory) not in output
    assert "unsupported compiler option" in output
    if failure != "unavailable":
        assert "unknown type name 'fixture_type'" in output
        assert "libavcodec/example.o" in output
    summary = json.loads((diagnostics_directory / f"{label}-failure.json").read_text())
    assert summary == {"stage": label, "reason": "exit-2" if failure == "exit" else failure, "redacted": True}


def test_redaction_handles_windows_msys_paths_and_structured_credentials(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", Path("D:/a/project/project"))
    text = (
        'D:\\a\\project\\project\\source.c\n/d/a/project/project/source.c\n'
        '/cygdrive/d/a/project/project/source.c\nC:/msys64/mingw64/bin/gcc.exe\n'
        '{"apiKey": "synthetic-key", "access_token": "synthetic-token"}\n'
        'Authorization: Bearer synthetic-auth\nhttps://user:synthetic-password@host.invalid/\n'
        '\x1b[31merror: unresolved fixture_symbol\x1b[0m\n'
    )
    result = build_bundle.redact_ffmpeg_diagnostic(text, ["C:/msys64/usr/bin/make.exe"], tmp_path)
    for sensitive in ("D:", "/d/a/", "/cygdrive/d/", "C:/msys64", "synthetic-key", "synthetic-token", "synthetic-auth", "synthetic-password", "\x1b"):
        assert sensitive not in result
    assert "error: unresolved fixture_symbol" in result


@pytest.mark.parametrize("diagnostics_enabled,label", [(False, "ffmpeg-compile"), (True, "production-export")])
def test_other_commands_and_default_mode_do_not_publish_logs(tmp_path: Path, monkeypatch, diagnostics_enabled: bool, label: str):
    monkeypatch.setattr(build_bundle.subprocess, "run", lambda *arguments, **options: subprocess.CompletedProcess([], 2, b"private output", b""))
    directory = tmp_path / "diagnostics"
    with pytest.raises(build_bundle.RuntimeBlocked):
        build_bundle.run_checked(["make"], cwd=tmp_path, label=label, ffmpeg_diagnostics_dir=directory if diagnostics_enabled else None)
    assert not directory.exists()


@pytest.mark.parametrize("failure_mode", ["io-error", "inside-source"])
def test_diagnostic_write_failure_does_not_mask_compiler_error(tmp_path: Path, monkeypatch, failure_mode: str):
    source_directory = tmp_path / "build/source"
    source_directory.mkdir(parents=True)
    directory = source_directory / "diagnostics" if failure_mode == "inside-source" else tmp_path / "blocked"
    if failure_mode == "io-error":
        directory.write_text("not a directory")
    monkeypatch.setattr(build_bundle.subprocess, "run", lambda *arguments, **options: subprocess.CompletedProcess([], 2, b"", b"error"))
    with pytest.raises(build_bundle.RuntimeBlocked, match="ffmpeg-compile-exit-2"):
        build_bundle.run_checked(["make"], cwd=source_directory, label="ffmpeg-compile", ffmpeg_diagnostics_dir=directory)
    if failure_mode == "inside-source":
        assert not directory.exists()


def test_long_logs_are_bounded_without_leaking_truncated_secret(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "FFMPEG_DIAGNOSTIC_LIMIT", 128)
    secret_value = "synthetic-secret-" * 30
    monkeypatch.setenv("ARK_API_KEY", secret_value)
    result = build_bundle.redact_ffmpeg_diagnostic("x" * 500 + secret_value + "\nerror: final compiler failure", ["make"], tmp_path)
    assert len(result) <= 128 + len("[earlier output omitted]\n")
    assert "synthetic-secret" not in result
    assert result.endswith("error: final compiler failure")
