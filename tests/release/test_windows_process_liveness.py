"""Windows liveness queries never send console events or terminate a process."""
from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant import lifecycle  # noqa: E402


@pytest.fixture
def windows_process_api(monkeypatch):
    api = SimpleNamespace(
        OpenProcess=Mock(return_value=90001),
        WaitForSingleObject=Mock(return_value=258),
        CloseHandle=Mock(), WAIT_OBJECT_0=0, WAIT_TIMEOUT=258,
    )
    monkeypatch.setitem(sys.modules, "_winapi", api)
    monkeypatch.setattr(lifecycle, "sys", SimpleNamespace(platform="win32"))

    def forbidden_signal(*arguments):
        pytest.fail("Windows liveness query attempted to send a process signal")

    monkeypatch.setattr(lifecycle.os, "kill", forbidden_signal)
    return api


@pytest.mark.parametrize("wait_result,expected", [(258, True), (0, False), (128, None)])
def test_windows_query_uses_only_zero_wait_and_closes_handle(windows_process_api, wait_result, expected):
    api = windows_process_api
    api.WaitForSingleObject.return_value = wait_result
    assert lifecycle._is_process_alive(12345) is expected
    api.OpenProcess.assert_called_once_with(0x00100000, False, 12345)
    api.WaitForSingleObject.assert_called_once_with(90001, 0)
    api.CloseHandle.assert_called_once_with(90001)


@pytest.mark.parametrize("windows_error,expected", [(87, False), (5, None), (6, None)])
def test_windows_open_failure_is_unknown_except_confirmed_missing_pid(windows_process_api, windows_error, expected):
    error = OSError("synthetic open failure")
    error.winerror = windows_error
    windows_process_api.OpenProcess.side_effect = error
    assert lifecycle._is_process_alive(12345) is expected
    windows_process_api.WaitForSingleObject.assert_not_called()
    windows_process_api.CloseHandle.assert_not_called()


def test_windows_wait_failure_is_unknown_and_still_closes_handle(windows_process_api):
    windows_process_api.WaitForSingleObject.side_effect = OSError("synthetic wait failure")
    assert lifecycle._is_process_alive(12345) is None
    windows_process_api.CloseHandle.assert_called_once_with(90001)


@pytest.mark.parametrize("process_id", [0, -1, True, "12345"])
def test_invalid_process_identity_never_reaches_windows_api(windows_process_api, process_id):
    assert lifecycle._is_process_alive(process_id) is None
    windows_process_api.OpenProcess.assert_not_called()


@pytest.mark.parametrize("failure,expected", [
    (None, True), (ProcessLookupError(), False), (PermissionError(), None),
    (OSError(errno.ESRCH, "synthetic missing process"), False),
])
def test_posix_query_keeps_signal_zero_semantics(monkeypatch, failure, expected):
    monkeypatch.setattr(lifecycle, "sys", SimpleNamespace(platform="darwin"))
    signal_query = Mock(side_effect=failure)
    monkeypatch.setattr(lifecycle.os, "kill", signal_query)
    assert lifecycle._is_process_alive(12345) is expected
    signal_query.assert_called_once_with(12345, 0)


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows process handles")
@pytest.mark.parametrize("exit_status", [0, 259])
def test_native_query_does_not_stop_parent_or_child(monkeypatch, exit_status):
    # Guard the old implementation before calling it: never send a real Ctrl+C
    # during regression testing, even if the query accidentally regresses.
    def forbidden_signal(*arguments):
        pytest.fail("Native Windows query attempted os.kill")

    monkeypatch.setattr(lifecycle.os, "kill", forbidden_signal)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", f"import sys; sys.stdin.buffer.read(1); sys.exit({exit_status})"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        for _attempt in range(3):
            assert lifecycle._is_process_alive(os.getpid()) is True
            assert lifecycle._is_process_alive(child.pid) is True
            assert child.poll() is None
        child.communicate(input=b"x", timeout=10)
        assert child.returncode == exit_status
        # 259 is also STILL_ACTIVE; handle signaling, not exit-code equality,
        # must distinguish an exited process with that legitimate exit status.
        assert lifecycle._is_process_alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
