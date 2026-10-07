"""Simulate CPython's Windows audit payload without running business programs."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from setup.release import smoke_bundle as smoke


@pytest.fixture
def windows_audit(tmp_path: Path, monkeypatch):
    bundle = tmp_path / "bundle with spaces"
    sandbox = tmp_path / "isolated user"
    sandbox.mkdir()
    python_path = bundle / "runtime/python/python.exe"
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"synthetic executable; never run")
    launcher = bundle / "app/scripts/release_launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# fixture only\n", encoding="utf-8")
    monkeypatch.setattr(smoke, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("ZN_ASSISTANT_PORT", "32001")
    return smoke.OfflineAudit(bundle, sandbox, python_path), launcher


def make_windows_event(arguments, keywords):
    working_directory = keywords.get("cwd")
    return (
        None, subprocess.list2cmdline(arguments),
        str(working_directory) if working_directory is not None else None,
        keywords["env"],
    )


@pytest.mark.parametrize("essential_names", [
    ("SystemRoot", "WINDIR", "COMSPEC"),
    ("SYSTEMROOT", "WINDIR", "COMSPEC"),
    ("systemroot", "windir", "comspec"),
])
def test_windows_system_environment_survives_nested_child_filtering(
    windows_audit, essential_names,
):
    audit, launcher = windows_audit
    essential_values = ("C:/Windows", "C:/Windows", "C:/Windows/System32/cmd.exe")
    parent = dict(zip(essential_names, essential_values))
    parent.update(PATH="C:/developer/tools", LLM_API_KEY="synthetic-secret", PYTHONPATH="C:/source")
    for child_generation in range(3):
        original_parent = dict(parent)
        keywords = {"cwd": audit.sandbox, "env": parent}
        audit.prepare_child([str(audit.python_path), "-I", "-B", str(launcher), "diagnose"], keywords)
        assert parent == original_parent
        environment = keywords["env"]
        assert environment["SystemRoot"] == essential_values[0], child_generation
        assert environment["WINDIR"] == essential_values[1]
        assert environment["COMSPEC"] == essential_values[2]
        assert environment["PATH"] == ""
        assert "LLM_API_KEY" not in environment and "PYTHONPATH" not in environment
        assert Path(environment["LOCALAPPDATA"]).is_relative_to(audit.sandbox)
        # Model Windows os.environ -> dict(os.environ) at the next hop.
        parent = {name.upper(): value for name, value in environment.items()}


@pytest.mark.parametrize("program", ["python", "ffmpeg"])
def test_only_prepared_exact_windows_event_is_accepted_once(windows_audit, program):
    audit, launcher = windows_audit
    observed_events = []

    class WindowsProcess:
        def __init__(self, arguments, **keywords):
            event = make_windows_event(arguments, keywords)
            audit("subprocess.Popen", event)
            observed_events.append((arguments, event))
            with pytest.raises(smoke.SmokeBlocked, match="audit-external-interpreter"):
                audit("subprocess.Popen", event)
            self.pid = 90101

    if program == "ffmpeg":
        audit.ffmpeg_path = audit.bundle_root / "runtime/ffmpeg/ffmpeg.exe"
        command = [str(audit.ffmpeg_path), "-version"]
        audit.approved_ffmpeg_commands.add(tuple(command))
    else:
        command = [str(audit.python_path), "-I", "-B", "-X", "utf8", str(launcher), "serve"]
    process_type = audit.guarded_process_type(WindowsProcess)
    process_type(command, cwd=audit.sandbox, env={"LLM_API_KEY": "synthetic-secret"})
    wrapped, event = observed_events[0]
    assert "with spaces" in event[1]
    assert "LLM_API_KEY" not in event[3]
    assert event[3]["PATH"] == ""
    assert audit.pending_windows_launch.event is None
    if program == "python":
        assert wrapped[:5] == [str(audit.python_path), "-I", "-B", "-X", "utf8"]
        assert "--guarded-launch" in wrapped
        marker = json.loads((audit.sandbox / "children/90101.json").read_text())
        assert marker["role"] == "release-server"


@pytest.mark.parametrize("changed_field", ["executable", "command", "cwd", "env"])
def test_modified_windows_event_is_rejected_and_permission_cleared(windows_audit, changed_field):
    audit, launcher = windows_audit

    class ModifiedWindowsProcess:
        def __init__(self, arguments, **keywords):
            event = list(make_windows_event(arguments, keywords))
            if changed_field == "executable":
                event[0] = str(audit.python_path)
            elif changed_field == "command":
                event[1] += " --unexpected-argument"
            elif changed_field == "cwd":
                event[2] = str(audit.bundle_root)
            else:
                event[3] = {**event[3], "PATH": "/external/tools"}
            audit("subprocess.Popen", tuple(event))

    process_type = audit.guarded_process_type(ModifiedWindowsProcess)
    with pytest.raises(smoke.SmokeBlocked, match="audit-external-interpreter"):
        process_type([str(audit.python_path), "-I", "-B", str(launcher), "serve"], cwd=audit.sandbox)
    assert audit.pending_windows_launch.event is None
    assert not audit.child_processes


def test_launch_permission_is_thread_local_and_cleared_on_creation_failure(windows_audit):
    audit, launcher = windows_audit
    thread_errors = []

    class FailedWindowsProcess:
        def __init__(self, arguments, **keywords):
            event = make_windows_event(arguments, keywords)

            def try_in_other_thread():
                try:
                    audit("subprocess.Popen", event)
                except smoke.SmokeBlocked as error:
                    thread_errors.append(str(error))

            thread = threading.Thread(target=try_in_other_thread)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert audit.pending_windows_launch.event is not None
            raise OSError("synthetic process creation failure")

    process_type = audit.guarded_process_type(FailedWindowsProcess)
    with pytest.raises(OSError, match="synthetic process creation failure"):
        process_type([str(audit.python_path), "-I", "-B", str(launcher), "serve"])
    assert thread_errors == ["audit-external-interpreter"]
    assert audit.pending_windows_launch.event is None


@pytest.mark.parametrize("invalid_kind", ["raw-string", "shell", "override", "external", "python-code", "python-option", "unapproved-ffmpeg"])
def test_invalid_calls_never_reach_underlying_process(windows_audit, invalid_kind):
    audit, launcher = windows_audit

    class NeverCalledProcess:
        def __init__(self, *arguments, **keywords):
            pytest.fail("Invalid call reached the underlying process")

    command = [str(audit.python_path), "-I", "-B", str(launcher)]
    keywords = {}
    if invalid_kind == "raw-string":
        command = subprocess.list2cmdline(command)
    elif invalid_kind == "shell":
        keywords["shell"] = True
    elif invalid_kind == "override":
        keywords["executable"] = str(audit.python_path)
    elif invalid_kind == "external":
        command[0] = str(audit.sandbox / "external-python.exe")
    elif invalid_kind == "python-code":
        command = [str(audit.python_path), "-c", "pass"]
    elif invalid_kind == "python-option":
        command = [str(audit.python_path), "-X", "other-option", str(launcher)]
    else:
        audit.ffmpeg_path = audit.bundle_root / "runtime/ffmpeg/ffmpeg.exe"
        command = [str(audit.ffmpeg_path), "-i", "https://remote.invalid"]
    with pytest.raises(smoke.SmokeBlocked):
        audit.guarded_process_type(NeverCalledProcess)(command, **keywords)
    assert getattr(audit.pending_windows_launch, "event", None) is None


def test_unwrapped_windows_string_event_is_rejected(windows_audit):
    audit, launcher = windows_audit
    command = subprocess.list2cmdline([str(audit.python_path), str(launcher)])
    with pytest.raises(smoke.SmokeBlocked, match="audit-external-interpreter"):
        audit("subprocess.Popen", (None, command, None, {}))


def test_native_popen_audit_event_with_unicode_spaced_paths(tmp_path: Path):
    # A disposable parent installs the real hook and launches a harmless stub.
    # Host Python is used for hook verification, NOT bundle-runtime acceptance.
    sandbox = tmp_path / "\u6d4b\u8bd5 sandbox with spaces"
    sandbox.mkdir()
    bundle = sandbox / "bundle"
    launcher = bundle / "app/scripts/release_launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# fixture only; never executed\n", encoding="utf-8")
    helper = sandbox / "guard fixture.py"
    helper.write_text(
        "import asyncio, json, os, socket, sys\n"
        "with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:\n"
        "    listener.bind(('127.0.0.1', 0))\n"
        "    listener.listen(1)\n"
        "event_loop = asyncio.new_event_loop()\n"
        "event_loop.close()\n"
        "print(json.dumps({'isolated': sys.flags.isolated, 'utf8': sys.flags.utf8_mode,\n"
        "                  'arguments': sys.argv[1:], 'socket_ready': True,\n"
        "                  'system_root_present': bool(os.environ.get('SystemRoot')),\n"
        "                  'path_empty': os.environ.get('PATH') == ''}))\n",
        encoding="utf-8",
    )
    program = f"""
import json, os, pathlib, subprocess, sys
sys.path.insert(0, {str(Path(smoke.__file__).resolve().parents[2])!r})
from setup.release.smoke_bundle import OfflineAudit
sandbox = pathlib.Path({str(sandbox)!r})
os.environ['ZN_ASSISTANT_PORT'] = '32001'
audit = OfflineAudit(pathlib.Path({str(bundle)!r}), sandbox, pathlib.Path(sys.executable), source=pathlib.Path({str(helper)!r}))
audit.install()
flags = ['-I', '-B', '-X', 'utf8'] if sys.platform == 'win32' else ['-I', '-B']
result = subprocess.run([sys.executable, *flags, {str(launcher)!r}, 'serve'], cwd=sandbox, capture_output=True, text=True, encoding='utf-8', check=True)
print(result.stdout.strip())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program], cwd=sandbox,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["isolated"] == 1
    assert report["socket_ready"] is True and report["path_empty"] is True
    if sys.platform == "win32":
        assert report["utf8"] == 1
        assert report["system_root_present"] is True
    assert report["arguments"] == ["--guarded-launch", str(bundle), str(sandbox), str(launcher), "serve"]
    assert not (sandbox / "audit-violations.jsonl").exists()
