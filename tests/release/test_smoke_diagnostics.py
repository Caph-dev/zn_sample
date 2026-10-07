"""Failure evidence survives temporary smoke cleanup without platform access."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from setup.release import smoke_bundle as smoke


def test_diagnose_exit_and_redacted_streams_survive_sandbox_removal(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-parent-secret")
    destination = tmp_path / "release/smoke-diagnostics"
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        sandbox = Path(temporary)
        launcher = sandbox / "fixture-launcher.py"
        launcher.write_text(
            "import sys\n"
            "print('configuration-required')\n"
            "print('ModuleNotFoundError: No module named fixture_dependency', file=sys.stderr)\n"
            "print('api_key=synthetic-parent-secret', file=sys.stderr)\n"
            "raise SystemExit(2)\n", encoding="utf-8",
        )
        with pytest.raises(smoke.SmokeBlocked, match="launcher-diagnose-unexpected-exit"):
            smoke.run_smoke_launcher(Path(sys.executable), launcher, sandbox, ("diagnose",), timeout=10)
        smoke.export_smoke_failure_diagnostics(sandbox, destination)
    assert not sandbox.exists()
    metadata = json.loads((destination / "launcher-diagnose-result.json").read_text())
    assert metadata["stage"] == "launcher" and metadata["mode"] == "diagnose"
    assert metadata["returncode"] == 2 and metadata["expected_success"] is True
    assert "configuration-required" in (destination / "launcher-diagnose-stdout.log").read_text()
    stderr = (destination / "launcher-diagnose-stderr.log").read_text()
    assert "ModuleNotFoundError" in stderr and "fixture_dependency" in stderr
    assert "synthetic-parent-secret" not in stderr


@pytest.mark.parametrize("outcome", ["failure", "timeout", "success", "unavailable"])
def test_controller_exports_failure_only_and_preserves_cleanup(tmp_path: Path, monkeypatch, outcome: str):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    destination = tmp_path / "release/diagnostics"
    events = []

    class FixtureController:
        returncode = 0 if outcome == "success" else 2

        def __init__(self, *arguments, **options):
            checkpoint = json.loads((destination / "outer-started-result.json").read_text())
            assert checkpoint["stage"] == "started" and checkpoint["returncode"] is None
            if outcome == "unavailable":
                raise FileNotFoundError("synthetic-secret must not be persisted")

        def communicate(self, timeout):
            if outcome == "timeout" and timeout != 5:
                raise subprocess.TimeoutExpired([], timeout, output=b"partial output", stderr=b"TimeoutError: fixture")
            smoke.record_smoke_process(
                sandbox, stage="launcher", mode="diagnose", returncode=2,
                expected_success=True, stdout="", stderr="ValueError: synthetic failure",
            )
            return b"controller output", b"Offline smoke blocked: launcher-diagnose-unexpected-exit"

    monkeypatch.setattr(smoke.subprocess, "Popen", FixtureController)
    monkeypatch.setattr(smoke, "terminate_smoke_process_group", lambda process: events.append("group-cleanup"))
    monkeypatch.setattr(smoke, "stop_registered_smoke_server", lambda *arguments: events.append("server-cleanup"))
    options = dict(
        cwd=sandbox, environment={}, process_options={}, sandbox=sandbox,
        bundle_root=sandbox / "bundle", timeout=10, diagnostics_dir=destination,
    )
    if outcome == "timeout":
        with pytest.raises(smoke.SmokeBlocked, match="smoke-outer-timeout-process-group-cleaned"):
            smoke.run_smoke_controller(["fixture-python"], **options)
        assert events == ["server-cleanup", "group-cleanup"]
    elif outcome == "unavailable":
        with pytest.raises(FileNotFoundError):
            smoke.run_smoke_controller(["fixture-python"], **options)
        assert events == []
    else:
        result = smoke.run_smoke_controller(["fixture-python"], **options)
        assert result.returncode == (0 if outcome == "success" else 2)
        assert events == ["group-cleanup"]
    if outcome == "success":
        assert {path.name for path in destination.iterdir()} == {
            "outer-started-result.json", "outer-returned-result.json", "outer-cleaned-result.json",
        }
        assert json.loads((destination / "outer-returned-result.json").read_text())["returncode"] == 0
    else:
        metadata = json.loads((destination / "controller-result.json").read_text())
        assert metadata["stage"] == "controller" and metadata["mode"] == "inside"
        assert metadata["returncode"] == (2 if outcome == "failure" else None)
        assert metadata["failure_type"] == {"failure": None, "timeout": "TimeoutExpired", "unavailable": "FileNotFoundError"}[outcome]
        if outcome == "timeout":
            assert (destination / "controller-stdout.log").read_text() == "partial output"


def test_diagnostic_io_failure_does_not_mask_launcher_failure(tmp_path: Path, monkeypatch):
    (tmp_path / "smoke-diagnostics").write_text("not a directory")
    monkeypatch.setattr(smoke.subprocess, "run", lambda *arguments, **options: subprocess.CompletedProcess([], 2, "", "error"))
    with pytest.raises(smoke.SmokeBlocked, match="launcher-diagnose-unexpected-exit"):
        smoke.run_smoke_launcher(Path("fixture-python"), Path("fixture-launcher"), tmp_path, ("diagnose",), timeout=10)


def test_launcher_timeout_retains_partial_evidence_without_converting_failure(tmp_path: Path, monkeypatch):
    def timeout_command(*arguments, **options):
        raise subprocess.TimeoutExpired([], 10, output=b"partial stdout", stderr=b"fixture stderr")

    monkeypatch.setattr(smoke.subprocess, "run", timeout_command)
    with pytest.raises(subprocess.TimeoutExpired):
        smoke.run_smoke_launcher(Path("fixture-python"), Path("fixture-launcher"), tmp_path, ("start",), timeout=10)
    metadata = json.loads((tmp_path / "smoke-diagnostics/launcher-start-result.json").read_text())
    assert metadata["returncode"] is None and metadata["failure_type"] == "TimeoutExpired"


def test_first_failure_is_not_overwritten_by_cleanup(tmp_path: Path):
    options = dict(sandbox=tmp_path, stage="launcher", mode="diagnose", expected_success=True)
    smoke.record_smoke_process(**options, returncode=2, stdout="original output", stderr="original error")
    smoke.record_smoke_process(**options, returncode=0, stdout="replacement output", stderr="")
    assert (tmp_path / "smoke-diagnostics/launcher-diagnose-stderr.log").read_text() == "original error"
    assert json.loads((tmp_path / "smoke-diagnostics/launcher-diagnose-result.json").read_text())["returncode"] == 2


def test_export_whitelist_no_overwrite_and_outer_credential_redaction(tmp_path: Path, monkeypatch):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    source = sandbox / "smoke-diagnostics"
    source.mkdir()
    (source / "launcher-diagnose-stderr.log").write_text("bare synthetic-parent-secret\n")
    (source / "config.toml").write_text("private fixture config")
    (source / "assistant.sqlite3").write_bytes(b"private fixture database")
    external = tmp_path / "private.log"
    external.write_text("private fixture log")
    (source / "controller-stdout.log").symlink_to(external)
    monkeypatch.setenv("ARK_API_KEY", "synthetic-parent-secret")
    destination = tmp_path / "release/diagnostics"
    smoke.export_smoke_failure_diagnostics(sandbox, destination)
    assert {path.name for path in destination.iterdir()} == {"launcher-diagnose-stderr.log"}
    report = destination / "launcher-diagnose-stderr.log"
    assert "synthetic-parent-secret" not in report.read_text()
    report.write_text("retained evidence")
    smoke.export_smoke_failure_diagnostics(sandbox, destination)
    assert report.read_text() == "retained evidence"


def test_output_redaction_preserves_exception_type_but_not_paths_or_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(smoke, "SMOKE_DIAGNOSTIC_LIMIT", 1024)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-gh-token")
    text = (
        'Traceback (most recent call last):\n'
        '  File "C:\\Users\\Private Person\\app.py", line 3, in main\n'
        'ValueError: fixture parse failed\n'
        'exception=ReleasePathError; code=release-resource-missing\n'
        '{"config_path": "C:\\\\Users\\\\Private Person\\\\config.toml"}\n'
        'api_key="synthetic-unknown-key"\nAuthorization: Bearer synthetic-auth\n'
        'synthetic-gh-token\nhttps://user:synthetic-password@host.invalid/\n'
        '\x1b[31merror\x1b[0m\n'
    )
    result = smoke.redact_smoke_output(text, tmp_path)
    for sensitive in ("Private Person", "synthetic-gh-token", "synthetic-unknown-key", "synthetic-auth", "synthetic-password", "\x1b"):
        assert sensitive not in result
    assert "ValueError: fixture parse failed" in result
    assert "ReleasePathError" in result and "release-resource-missing" in result
    result = smoke.redact_smoke_output("x" * 2000 + text, tmp_path)
    assert len(result) <= 1024 + len("[earlier output omitted]\n")


def test_cli_rejects_diagnostic_output_inside_bundle_before_runtime_access(tmp_path: Path):
    bundle = tmp_path / "bundle"
    with pytest.raises(smoke.SmokeBlocked, match="smoke-diagnostics-inside-bundle"):
        smoke.main(["--bundle", str(bundle), "--offline", "--diagnostics-dir", str(bundle / "diagnostics")])


@pytest.mark.parametrize("operator_mode", ["prepare", "screen", "pipeline", "tracking"])
def test_busy_operator_rejection_can_be_recorded_without_changing_gate(tmp_path: Path, monkeypatch, operator_mode):
    captured_commands = []

    def reject_busy_operator(command, **options):
        captured_commands.append(command)
        return subprocess.CompletedProcess(command, 2, "", "instance-busy-or-unknown")

    monkeypatch.setattr(smoke.subprocess, "run", reject_busy_operator)
    result = smoke.run_smoke_launcher(
        Path("fixture-python"), Path("fixture-launcher"), tmp_path, ("operator", operator_mode),
        timeout=10, expected_success=False,
    )
    assert result.returncode == 2
    assert captured_commands[0][-2:] == ["operator", operator_mode]
    destination = tmp_path.parent / (tmp_path.name + "-reports")
    smoke.export_smoke_failure_diagnostics(tmp_path, destination)
    metadata = json.loads((destination / "launcher-operator-result.json").read_text())
    assert metadata["mode"] == "operator" and metadata["expected_success"] is False
    assert (destination / "launcher-operator-stderr.log").read_text() == "instance-busy-or-unknown"


def test_outer_checkpoint_survives_abrupt_child_exit_without_claiming_success(tmp_path: Path, monkeypatch):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    destination = tmp_path / "reports"
    monkeypatch.setattr(smoke, "terminate_smoke_process_group", lambda process: None)
    result = smoke.run_smoke_controller(
        [sys.executable, "-I", "-B", "-c", "import os; os._exit(1)"],
        cwd=sandbox, environment={}, process_options={}, sandbox=sandbox,
        bundle_root=sandbox / "bundle", timeout=10, diagnostics_dir=destination,
    )
    assert result.returncode == 1 and not result.stdout and not result.stderr
    assert json.loads((destination / "outer-returned-result.json").read_text())["returncode"] == 1
    assert json.loads((destination / "controller-result.json").read_text())["returncode"] == 1


def test_outer_checkpoint_failure_is_nonfatal_and_existing_evidence_is_preserved(tmp_path: Path):
    destination = tmp_path / "not-a-directory"
    destination.write_text("original fixture evidence")
    smoke.record_outer_smoke_checkpoint(destination, "started")
    assert destination.read_text() == "original fixture evidence"
    smoke.record_outer_smoke_checkpoint(tmp_path, "started", 1)
    smoke.record_outer_smoke_checkpoint(tmp_path, "started", 0)
    assert json.loads((tmp_path / "outer-started-result.json").read_text())["returncode"] == 1


@pytest.mark.parametrize("identity_error", [None, "marker-pid", "marker-role", "marker-script", "health"])
def test_cleanup_fallback_only_signals_bound_healthy_smoke_child(tmp_path: Path, monkeypatch, identity_error):
    sandbox = tmp_path / "sandbox"
    bundle = sandbox / "bundle"
    state_path = smoke.sandbox_user_data_dir(sandbox) / "runtime/state.json"
    state_path.parent.mkdir(parents=True)
    state = {"pid": 12345, "port": 32001, "instance_id": "synthetic-instance", "version": "0.1.0", "target": "test"}
    state_path.write_text(json.dumps(state))
    marker_directory = sandbox / "children"
    marker_directory.mkdir()
    marker = {"pid": 12345, "role": "release-server", "script": str(bundle / "app/scripts/release_launcher.py")}
    if identity_error == "marker-pid":
        marker["pid"] = 54321
    elif identity_error == "marker-role":
        marker["role"] = "smoke-helper"
    elif identity_error == "marker-script":
        marker["script"] = str(tmp_path / "unrelated-script.py")
    (marker_directory / "12345.json").write_text(json.dumps(marker))
    health = {**state, "instance_id": "different-instance"} if identity_error == "health" else state
    monkeypatch.setattr(smoke, "request_local", lambda *arguments: (200, json.dumps(health).encode(), {}))
    signals = []

    def signal_fixture(process_id, signal_value):
        signals.append((process_id, signal_value))
        state_path.unlink()

    monkeypatch.setattr(smoke.os, "kill", signal_fixture)
    assert smoke.stop_registered_smoke_server(sandbox, bundle, timeout=0.1) is (identity_error is None)
    assert signals == ([(12345, smoke.signal.SIGTERM)] if identity_error is None else [])
