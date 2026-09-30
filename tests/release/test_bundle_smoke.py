"""Offline regressions for the smoke tripwire and fixture-only local HTTP.

These tests do not claim full-bundle acceptance: the integrated smoke must run
with the actual private interpreter. Audit hooks are tested in disposable
processes so pytest's own interpreter and plugins remain unaffected.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SOURCE = PROJECT_ROOT / "setup/release/smoke_bundle.py"
specification = importlib.util.spec_from_file_location("bundle_smoke_test_subject", SMOKE_SOURCE)
assert specification is not None and specification.loader is not None
smoke = importlib.util.module_from_spec(specification)
specification.loader.exec_module(smoke)


@pytest.fixture
def release_fixture(tmp_path):
    bundle = tmp_path / "release with spaces"
    python_path = bundle / "runtime/python/bin/python3"
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"fixture binary; never executed")
    node_path = bundle / "runtime/node/bin/node"
    ffmpeg_path = bundle / "runtime/ffmpeg/ffmpeg"
    node_path.parent.mkdir(parents=True)
    ffmpeg_path.parent.mkdir(parents=True)
    node_path.write_bytes(b"fixture node; never executed")
    ffmpeg_path.write_bytes(b"fixture ffmpeg; never executed")
    launcher = bundle / "app/scripts/release_launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# fixture only\n", encoding="utf-8")
    manifest = {
        "schema_version": 1, "scope": "browser-application-bundle", "application_version": "0.1.0",
        "target": "macos-arm64", "executables": {
            "python_executable": "runtime/python/bin/python3", "node_executable": "runtime/node/bin/node",
            "ffmpeg_executable": "runtime/ffmpeg/ffmpeg",
        },
        "sources": {"ffmpeg": {"version": "9.0.2"}},
        "external_prerequisites": [{"name": "@ziniao-open/cli", "version": "1.0.7", "bundled": False}],
        "files": [{"path": path.relative_to(bundle).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size": path.stat().st_size}
                   for path in (python_path, node_path, ffmpeg_path, launcher)],
    }
    (bundle / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    sandbox = tmp_path / "synthetic user with spaces"
    sandbox.mkdir()
    return bundle, python_path, manifest, sandbox


def write_manifest(bundle, manifest):
    (bundle / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_private_python_resolution_never_discovers_host_tools(release_fixture, monkeypatch):
    bundle, python_path, manifest, _sandbox = release_fixture
    monkeypatch.setenv("PATH", "/fake/developer/tools")
    assert smoke.resolve_bundle_python(bundle) == (python_path, manifest)
    python_path.unlink()
    with pytest.raises(smoke.SmokeBlocked, match="private-python-required"):
        smoke.resolve_bundle_python(bundle)


@pytest.mark.parametrize("mutation,code", [
    (lambda manifest: manifest.update(scope="runtime-proof-not-full-application"), "application-resources-not-integrated"),
    (lambda manifest: manifest["executables"].update(python_executable="../outside/python"), "manifest-path-escape"),
    (lambda manifest: manifest["executables"].update(python_executable="C:/external/python.exe"), "manifest-path-escape"),
    (lambda manifest: manifest["files"][0].update(sha256="0" * 64), "private-python-hash-mismatch"),
    (lambda manifest: manifest.update(files=[]), "private-python-hash-required"),
])
def test_manifest_failures_do_not_fall_back(release_fixture, mutation, code):
    bundle, _python_path, manifest, _sandbox = release_fixture
    mutation(manifest)
    write_manifest(bundle, manifest)
    with pytest.raises(smoke.SmokeBlocked, match=code):
        smoke.resolve_bundle_python(bundle)


def test_private_python_symlink_cannot_escape_bundle(release_fixture, tmp_path):
    bundle, python_path, _manifest, _sandbox = release_fixture
    outside = tmp_path / "external-python"
    outside.write_bytes(python_path.read_bytes())
    python_path.unlink()
    python_path.symlink_to(outside)
    with pytest.raises(smoke.SmokeBlocked, match="manifest-path-escape"):
        smoke.resolve_bundle_python(bundle)


def test_manifest_pins_every_file_and_rejects_unlisted_payload(release_fixture):
    bundle, _python_path, _manifest, _sandbox = release_fixture
    assert smoke.verify_bundle_manifest(bundle)["target"] == "macos-arm64"
    (bundle / "unexpected.payload").write_bytes(b"unlisted")
    with pytest.raises(smoke.SmokeBlocked, match="manifest-payload-drift"):
        smoke.verify_bundle_manifest(bundle)


def test_bundle_relocation_is_unicode_spaced_and_readonly(release_fixture):
    bundle, _python_path, _manifest, sandbox = release_fixture
    relocated = smoke.make_readonly_relocated_bundle(bundle, sandbox)
    assert "安装目录 with spaces" in str(relocated)
    assert smoke.verify_bundle_manifest(relocated)["target"] == "macos-arm64"
    assert all(path.is_symlink() or (path.stat().st_mode & 0o222) == 0
               for path in (relocated, *relocated.rglob("*")))
    smoke.restore_bundle_write_permissions(relocated)


def test_user_environment_is_allowlisted_and_parent_is_unchanged(tmp_path):
    parent = {
        "HOME": "/real/user", "USERPROFILE": "/real/user", "APPDATA": "/real/roaming",
        "LOCALAPPDATA": "/real/local", "PATH": "/dev/bin", "PYTHONPATH": "/developer/source",
        "PYTHONHOME": "/external/python", "VIRTUAL_ENV": "/dev/venv", "CONDA_PREFIX": "/conda",
        "LLM_API_KEY": "synthetic-not-a-secret", "HTTPS_PROXY": "http://remote.invalid",
        "ZN_SAMPLE_CONFIG": "/real/config.toml", "ZN_SAMPLE_USER_DATA_DIR": "/real/state",
        "SystemRoot": "C:/Windows",
    }
    original = dict(parent)
    environment = smoke.isolated_environment(tmp_path, 31001, parent)
    assert parent == original
    assert environment["PATH"] == ""
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                 "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "TMP", "TEMP", "TMPDIR"):
        assert Path(environment[name]).resolve().is_relative_to(tmp_path)
        assert Path(environment[name]).is_dir()
    assert "ZN_SAMPLE_USER_DATA_DIR" not in environment
    assert environment["PYTHONTZPATH"] == ""
    assert smoke.sandbox_user_data_dir(tmp_path, platform_name="darwin") == (
        tmp_path / "home/Library/Application Support/ZnSampleAssistant"
    )
    assert smoke.sandbox_user_data_dir(tmp_path, platform_name="win32") == (
        tmp_path / "local/ZnSampleAssistant"
    )
    assert not set(environment).intersection({"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX",
                                              "LLM_API_KEY", "HTTPS_PROXY", "ZN_SAMPLE_CONFIG"})


@pytest.mark.parametrize("operation,expected", [
    ("open(outside, 'rb')", "audit-external-read"),
    ("open(outside, 'w')", "audit-external-write"),
    ("os.scandir(outside.parent)", "audit-external-read"),
    ("sqlite3.connect(outside)", "audit-external-write"),
    ("sqlite3.connect(outside.as_uri() + '?mode=ro', uri=True)", "audit-external-write"),
    ("socket.getaddrinfo('remote.invalid', 443)", "audit-remote-dns"),
    ("socket.socket().connect(('203.0.113.1', 443))", "audit-non-loopback-network"),
    ("socket.socket().connect(('127.0.0.1', 9481))", "audit-unregistered-loopback-network"),
    ("subprocess.run(['/external/python', '-c', 'pass'])", "audit-external-interpreter"),
    ("subprocess.run('echo unsafe', shell=True)", "audit-shell-or-executable-override"),
    ("subprocess.run([sys.executable, '-c', 'pass'])", "audit-unwrapped-python-command"),
    ("os.system('echo unsafe')", "audit-unwrapped-process"),
    ("sys.audit('import', 'fixture_external', str(outside), None, None, None)", "audit-external-read"),
])
def test_installed_audit_blocks_before_io_in_disposable_process(tmp_path, operation, expected):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "real user data"
    outside.write_bytes(b"must remain unchanged")
    # Host Python is used only to test the audit class, never to run application
    # acceptance. It is deliberately NOT passed through assert_private_runtime.
    program = f"""
import encodings.idna, importlib.util, json, os, pathlib, socket, sqlite3, subprocess, sys
spec = importlib.util.spec_from_file_location('smoke', {str(SMOKE_SOURCE)!r})
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
sandbox = pathlib.Path({str(sandbox)!r})
outside = pathlib.Path({str(outside)!r})
audit = smoke.OfflineAudit(sandbox / 'bundle', sandbox, pathlib.Path(sys.executable))
audit.install()
try:
    {operation}
except smoke.SmokeBlocked as error:
    print(str(error))
else:
    raise AssertionError('audit did not block')
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", program], capture_output=True,
                            text=True, encoding="utf-8", timeout=10, cwd=sandbox)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert outside.read_bytes() == b"must remain unchanged"
    events = [json.loads(line) for line in (sandbox / "audit-violations.jsonl").read_text().splitlines()]
    assert events[-1]["code"] == expected


def test_symlink_into_real_user_data_is_blocked(release_fixture, tmp_path):
    bundle, python_path, _manifest, sandbox = release_fixture
    outside = tmp_path / "real-config"
    outside.write_text("fixture", encoding="utf-8")
    alias = sandbox / "config-alias"
    alias.symlink_to(outside)
    audit = smoke.OfflineAudit(bundle, sandbox, python_path)
    with pytest.raises(smoke.SmokeBlocked, match="audit-external-read"):
        audit.check_path(alias)


def test_bundle_is_readonly_but_sandbox_is_writable(release_fixture):
    bundle, python_path, _manifest, sandbox = release_fixture
    audit = smoke.OfflineAudit(bundle, sandbox, python_path)
    audit.check_path(python_path)
    audit.check_path(sandbox / "exports/new.csv", write=True)
    with pytest.raises(smoke.SmokeBlocked, match="audit-external-write"):
        audit.check_path(bundle / "app/config.toml", write=True)


def test_system_resource_allowlist_is_exact_and_readonly(release_fixture):
    bundle, python_path, _manifest, sandbox = release_fixture
    audit = smoke.OfflineAudit(bundle, sandbox, python_path)
    system_version_file = Path("/System/Library/CoreServices/SystemVersion.plist")
    audit.check_path(system_version_file)
    assert audit.allowed_system_reads == {"macos-system-version"}
    with pytest.raises(smoke.SmokeBlocked, match="audit-external-read"):
        audit.check_path(system_version_file.with_name("unlisted-system-file.plist"))
    with pytest.raises(smoke.SmokeBlocked, match="audit-external-write"):
        audit.check_path(system_version_file, write=True)


def test_child_reentry_preserves_private_flags_and_installs_guard(release_fixture, monkeypatch):
    bundle, python_path, _manifest, sandbox = release_fixture
    monkeypatch.setenv("ZN_ASSISTANT_PORT", "32001")
    audit = smoke.OfflineAudit(bundle, sandbox, python_path)
    class DummyProcess:
        def __init__(self, arguments, **keywords):
            self.arguments = arguments
            self.environment = keywords["env"]
            self.pid = 99123

    launcher = bundle / "app/scripts/release_launcher.py"
    guarded_process = audit.guarded_process_type(DummyProcess)
    assert issubclass(guarded_process, DummyProcess)
    tracked_process = type("TrackedProcess", (guarded_process,), {})
    assert issubclass(tracked_process, guarded_process)
    result = tracked_process([str(python_path), "-I", "-B", str(launcher), "serve"],
                             cwd=bundle / "app", env={"HOME": "/real/user", "PATH": "/dev/bin",
                                                       "LLM_API_KEY": "fixture-discard-me", "PYTHONPATH": "/dev/source"})
    assert isinstance(result, DummyProcess)
    command = result.arguments
    assert command[:4] == [str(python_path), "-I", "-B", str(SMOKE_SOURCE)]
    assert command[4:] == ["--guarded-launch", str(bundle), str(sandbox), str(launcher), "serve"]
    environment = result.environment
    assert Path(environment["HOME"]).is_relative_to(sandbox)
    assert environment["PATH"] == ""
    assert "LLM_API_KEY" not in environment and "PYTHONPATH" not in environment
    marker = json.loads((sandbox / "children/99123.json").read_text(encoding="utf-8"))
    assert marker["role"] == "release-server"


def test_private_runtime_rejects_development_interpreter(release_fixture):
    bundle, python_path, _manifest, _sandbox = release_fixture
    with pytest.raises(smoke.SmokeBlocked, match="smoke-private-python-required"):
        smoke.assert_private_runtime(python_path, bundle)


def test_ffmpeg_execution_requires_pinned_path_and_exact_command(release_fixture):
    bundle, _python_path, manifest, sandbox = release_fixture
    audit = smoke.OfflineAudit(bundle, sandbox, bundle / manifest["executables"]["python_executable"])
    audit.ffmpeg_path = (bundle / manifest["executables"]["ffmpeg_executable"]).resolve()
    allowed_command = (str(audit.ffmpeg_path), "-version")
    audit.approved_ffmpeg_commands.add(allowed_command)
    audit("subprocess.Popen", (str(audit.ffmpeg_path), list(allowed_command), None, None))
    with pytest.raises(smoke.SmokeBlocked, match="audit-external-interpreter"):
        audit("subprocess.Popen", (str(audit.ffmpeg_path), [str(audit.ffmpeg_path), "-i", "remote"], None, None))
    with pytest.raises(smoke.SmokeBlocked, match="audit-external-interpreter"):
        audit("subprocess.Popen", ("/usr/bin/ffmpeg", ["/usr/bin/ffmpeg", "-version"], None, None))


def test_outer_cleanup_stops_only_its_process_group():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                               start_new_session=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    smoke.terminate_smoke_process_group(process, timeout=0.1)
    assert process.poll() is not None


def test_offline_flag_is_mandatory(release_fixture):
    bundle, _python_path, _manifest, _sandbox = release_fixture
    with pytest.raises(SystemExit) as exit_info:
        smoke.main(["--bundle", str(bundle)])
    assert exit_info.value.code == 2


def test_actual_http_helper_handles_health_export_and_refuses_redirect(monkeypatch):
    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true,"instance_id":"fixture","version":"0.1.0"}')
            else:
                self.send_response(302)
                self.send_header("Location", "https://remote.invalid/business")
                self.end_headers()

        def do_POST(self):
            assert self.headers["Origin"] == f"http://127.0.0.1:{self.server.server_port}"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert payload == {"kind": "today"}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"job_id":"synthetic"}')

        def log_message(self, *arguments):
            return

    monkeypatch.setenv("HTTP_PROXY", "http://remote.invalid/proxy")
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body, _headers = smoke.request_local(server.server_port, "/api/health")
        assert status == 200 and json.loads(body)["instance_id"] == "fixture"
        assert smoke.request_local(server.server_port, "/api/jobs/report-export", payload={"kind": "today"})[0] == 200
        assert smoke.request_local(server.server_port, "/redirect")[0] == 302
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
