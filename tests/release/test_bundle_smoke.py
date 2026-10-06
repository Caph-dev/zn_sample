"""Offline regressions for the smoke tripwire and fixture-only local HTTP.

These tests do not claim full-bundle acceptance: the integrated smoke must run
with the actual private interpreter. Audit hooks are tested in disposable
processes so pytest's own interpreter and plugins remain unaffected.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from setup.release import resources


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


def run_isolated_cleanup_resource_smoke():
    """Host-interpreter offline code/resource smoke, NOT private-runtime acceptance.

    Executed from its source in a disposable -I -B process. Only registered app
    files and existing host dependencies are available; all business I/O is
    blocked, while the real routes, migrations, queue, handler and core run.
    """
    import importlib
    import json
    import mimetypes
    import os
    import re
    import subprocess
    import sys
    import uuid
    from contextlib import ExitStack
    from datetime import timedelta
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from urllib.parse import parse_qs, urlsplit

    sandbox_root = Path(sys.argv[1]).resolve()
    source_root = Path(sys.argv[2]).resolve()
    application_root = sandbox_root / "resources" / "app"
    user_root = sandbox_root / "synthetic user data"
    runtime_roots = (Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())
    # Homebrew's stdlib sysconfig reads this exact platform metadata file.
    allowed_system_reads = {Path("/System/Library/CoreServices/SystemVersion.plist")} if sys.platform == "darwin" else set()
    blocked_events = []

    def reject_boundary(code):
        blocked_events.append(code)
        raise AssertionError(code)

    def require_isolated_path(value, *, write=False):
        if value is None or isinstance(value, int):
            return
        resolved_path = Path(os.fsdecode(value)).resolve()
        if resolved_path.is_relative_to(application_root):
            if write:
                reject_boundary("installation-write-blocked")
            return
        if resolved_path.is_relative_to(sandbox_root):
            return
        if not write and any(resolved_path.is_relative_to(root) for root in runtime_roots):
            return
        if not write and resolved_path in allowed_system_reads:
            return
        reject_boundary("unregistered-read-or-write-blocked")

    def audit_offline_boundaries(event, arguments):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname"}:
            reject_boundary("network-blocked")
        if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork"}:
            reject_boundary("real-child-blocked")
        if event == "open":
            mode, flags = arguments[1:3]
            write = (isinstance(mode, str) and any(marker in mode for marker in "wax+")) or (
                isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            )
            require_isolated_path(arguments[0], write=write)
        elif event in {"os.listdir", "os.scandir", "import"}:
            require_isolated_path(arguments[1] if event == "import" else arguments[0])
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "sqlite3.connect"}:
            require_isolated_path(arguments[0], write=True)
        elif event in {"os.rename", "os.link", "os.symlink"}:
            for value in arguments[:2]:
                require_isolated_path(value, write=True)

    sys.addaudithook(audit_offline_boundaries)
    # Exercise the tripwire itself without opening a source file or a socket.
    for event, arguments, expected in (
        ("open", (str(source_root / "AGENTS.md"), "r", 0), "unregistered-read-or-write-blocked"),
        ("socket.connect", (None, ("127.0.0.1", 9481)), "network-blocked"),
        ("subprocess.Popen", (sys.executable, [sys.executable], None, None), "real-child-blocked"),
        ("open", (str(application_root / "config.toml"), "w", os.O_WRONLY), "installation-write-blocked"),
    ):
        try:
            sys.audit(event, *arguments)
        except AssertionError as error:
            assert str(error) == expected
        else:
            raise AssertionError("Offline tripwire did not block")
    assert len(blocked_events) == 4
    blocked_events.clear()

    sys.path[:0] = [str(application_root), str(application_root / "scripts")]
    assert not (application_root.parent / "zn_daren").exists()
    assert not any(Path(value).resolve() in {source_root, source_root.parent / "zn_daren"}
                   for value in sys.path if value)

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from fastapi.testclient import TestClient
    from sqlalchemy import inspect as inspect_database, select, text
    from sqlalchemy.orm import sessionmaker

    from assistant import bootstrap, paths
    from assistant.app import create_app
    from assistant.database.engine import create_database_engine
    from assistant.database.models import Job, TargetCleanupBatch, TargetCleanupItem
    from assistant.jobs import registry
    from assistant.jobs.handlers import target_cleanup as handler
    from assistant.jobs.worker import worker_loop_once
    from assistant.services import target_cleanup_service as service
    from lib import target_invitation_dom, target_invitation_navigation, target_plan_cleanup, zclaw, zclaw_cli

    cleanup_script = importlib.import_module("cleanup_target_plans")
    assert paths.application_resource_dir() == application_root
    assert paths.is_packaged_distribution() is False  # No fabricated private-runtime manifest.
    configuration = Config(str(application_root / "assistant/database/alembic.ini"))
    bootstrap.configure_alembic_paths(configuration)
    head_revision = ScriptDirectory.from_config(configuration).get_revision("head")
    assert head_revision.revision == "0007_target_cleanup"
    assert Path(head_revision.module.__file__).resolve() == (
        application_root / "assistant/database/migrations/versions/0007_target_cleanup.py"
    )

    nonce = uuid.uuid4().hex
    synthetic_identity = {
        "store_id": "fixture-store-" + nonce, "store_name": "Synthetic offline store",
        "shop_id": "fixture-shop-" + nonce, "shop_region": "US",
    }
    identity = Mock(return_value=synthetic_identity)
    forbidden = Mock(side_effect=AssertionError("Unmocked business boundary"))
    synthetic_cancellations = []
    child_modes = []

    def navigate_synthetic_store(store_id):
        assert store_id == synthetic_identity["store_id"]
        return {"destination": {"shop_id": synthetic_identity["shop_id"]}}

    def scan_synthetic_invitations(store_id, *, cutoff):
        assert store_id == synthetic_identity["store_id"]
        identifier_prefix = "fixture-invitation-" + nonce + "-" + cutoff.isoformat()
        rows = [{
            "invitation_id": identifier_prefix + suffix, "name": "Synthetic " + suffix,
            "last_modified": modified_date.isoformat(),
            "accepted_count": 2, "promoted_count": 1, "invited_count": 5,
        } for suffix, modified_date in (("-old", cutoff - timedelta(days=1)), ("-boundary", cutoff))]
        return {"rows": rows, "scan_complete": True, "stop_reason": "first-page", "pages_scanned": 1}

    def locate_synthetic_invitation(store_id, **parameters):
        assert store_id == synthetic_identity["store_id"]
        assert parameters["invitation_id"].startswith("fixture-invitation-" + nonce)
        return {"revalidated": True}

    def cancel_synthetic_invitation(store_id, **parameters):
        assert parameters.pop("execute") is True
        locate_synthetic_invitation(store_id, **parameters)
        invitation_id = parameters["invitation_id"]
        with session_factory() as session:
            item = session.scalar(select(TargetCleanupItem).where(TargetCleanupItem.invitation_id == invitation_id))
            assert item.status == "attempting" and item.attempted_at is not None
            artifacts = service.artifact_paths(item.batch_id)
            assert artifacts["backup_json"].is_file() and artifacts["backup_csv"].is_file()
            checkpoint = json.loads(artifacts["results_json"].read_text(encoding="utf-8"))
            assert checkpoint["items"][0]["status"] == "attempting"
        synthetic_cancellations.append(invitation_id)
        return {"status": "submitted", "action": "fixture-only", "reason": "No platform cancellation performed"}

    def run_fake_cleanup_child(arguments, **options):
        mode, batch_id, job_id = arguments[3], arguments[5], arguments[7]
        expected = [sys.executable, str(application_root / "scripts/cleanup_target_plans.py"),
                    "--mode", mode, "--batch-id", batch_id, "--job-id", job_id]
        if mode == "execute":
            expected.extend(["--execute", "--yes"])
        assert arguments == expected and mode in {"preview", "execute"}
        assert options["cwd"] == str(application_root)
        assert options["stdin"] == subprocess.DEVNULL and options["stderr"] == subprocess.STDOUT
        assert options["shell"] is False and options["check"] is False
        assert options["env"]["PYTHONUTF8"] == "1" and options["env"]["PYTHONIOENCODING"] == "utf-8"
        with session_factory() as session:
            job = session.get(Job, job_id)
            assert job.status == "running" and job.store_id == synthetic_identity["store_id"]
            assert not registry.can_request_cancellation(job.job_type, job.status)
        options["stdout"].write("Offline in-process fake child; no platform operations.\n")
        child_modes.append(mode)
        return SimpleNamespace(returncode=cleanup_script.run_batch(
            session_factory, batch_id=batch_id, job_id=job_id, mode=mode,
            execute=mode == "execute", yes=mode == "execute",
        ))

    with ExitStack() as guards:
        # CSV FileResponse uses built-in MIME types, not host /etc or registry configuration.
        guards.enter_context(patch.object(mimetypes, "knownfiles", []))
        guards.enter_context(patch.object(mimetypes.MimeTypes, "read_windows_registry", lambda self: None))
        mimetypes.init()
        guards.enter_context(patch.object(paths, "user_data_dir", lambda: user_root))
        guards.enter_context(patch.object(service, "resolve_running_identity", identity))
        for module, names in (
            (zclaw, ("zclaw_exec", "zclaw_invoke", "run_ziniao_cli", "list_running_stores",
                     "list_all_stores", "open_store", "close_store", "visit_page", "resolve_store_id")),
            (zclaw_cli, ("run_ziniao_cli", "resolve_ziniao_cli_command")),
            (target_invitation_dom, ("zclaw_exec",)),
            (target_invitation_navigation, ("zclaw_exec", "ensure_ongoing_tab")),
        ):
            for name in names:
                guards.enter_context(patch.object(module, name, forbidden))
        guards.enter_context(patch.object(target_invitation_navigation, "navigate_from_seller_home_to_ongoing",
                                         navigate_synthetic_store))
        guards.enter_context(patch.object(target_invitation_dom, "scan_older_invitations", scan_synthetic_invitations))
        guards.enter_context(patch.object(target_invitation_dom, "locate_target_invitation", locate_synthetic_invitation))
        guards.enter_context(patch.object(target_invitation_dom, "cancel_invitation_by_id", cancel_synthetic_invitation))
        guards.enter_context(patch.object(subprocess, "run", run_fake_cleanup_child))

        paths.ensure_user_dirs()
        sqlite_path = user_root / "assistant.sqlite3"
        assert not sqlite_path.exists()
        bootstrap.upgrade_database(sqlite_path=sqlite_path)
        engine = create_database_engine(sqlite_path)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        try:
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0007_target_cleanup"
            assert {"target_cleanup_batches", "target_cleanup_items"} <= set(inspect_database(engine).get_table_names())
            application = create_app(port=8765)
            application.state.session_factory = session_factory
            origin = {"Origin": "http://127.0.0.1:8765"}
            previews_url = "/api/target-cleanup/previews"
            batches = []

            def assert_bootstrap(response, batch_id=""):
                assert response.status_code == 200
                match = re.search(r'<script id="console-bootstrap" type="application/json">(.*?)</script>',
                                  response.text, re.DOTALL)
                payload = json.loads(match.group(1))
                assert payload["page"] == "plan_cleanup"
                assert payload["data"]["batch_id"] == batch_id and payload["data"]["read_error"] == ""

            with TestClient(application, base_url="http://127.0.0.1:8765") as client:
                assert_bootstrap(client.get("/plan-cleanup"))
                identity.assert_not_called()
                fields = {"months": "4", "idempotency_key": "fixture-preview-" + nonce}
                assert client.post(previews_url, data=fields).status_code == 403
                assert client.post(previews_url, headers={"Origin": "http://remote.invalid"}, data=fields).status_code == 403
                assert client.post(previews_url, headers={**origin, "Host": "remote.invalid"}, data=fields).status_code == 400
                assert client.post(previews_url, headers=origin, data={**fields, "store_id": "override"}).status_code == 400
                assert client.post(previews_url, headers=origin, json=fields).status_code == 415
                identity.assert_not_called()

                for months in (2, 4):
                    response = client.post(previews_url, headers=origin, data={
                        "months": str(months), "idempotency_key": f"fixture-preview-{nonce}-{months}",
                    })
                    assert response.status_code == 200
                    created = response.json()
                    assert worker_loop_once(session_factory) == created["job_id"]
                    payload = client.get(f"/api/target-cleanup/batches/{created['batch_id']}").json()
                    assert payload["months"] == months and payload["scan_complete"] and payload["can_execute"]
                    assert payload["scan_count"] == 2 and payload["candidate_count"] == payload["nonzero_count"] == 1
                    assert payload["jobs"]["preview"]["status"] == "succeeded"
                    assert len(payload["items"]) == 1 and payload["items"][0]["invitation_id"].endswith("-old")
                    assert_bootstrap(client.get(created["batch_url"]), created["batch_id"])
                    batches.append(created)
                assert synthetic_cancellations == []

                selected = batches[-1]
                execute_url = f"/api/target-cleanup/batches/{selected['batch_id']}/execute"
                execution_fields = {"confirmation": "y", "idempotency_key": "fixture-execute-" + nonce}
                assert client.post(execute_url, data=execution_fields).status_code == 403
                assert client.post(execute_url, headers=origin, data={**execution_fields, "confirmation": "yes"}).status_code == 400
                assert client.post(execute_url, headers=origin, data={**execution_fields, "limit": "1"}).status_code == 400
                assert synthetic_cancellations == [] and child_modes == ["preview", "preview"]
                response = client.post(execute_url, headers=origin, data=execution_fields)
                assert response.status_code == 200
                execution = response.json()
                assert worker_loop_once(session_factory) == execution["job_id"]
                duplicate = client.post(execute_url, headers=origin, data=execution_fields)
                assert duplicate.json() == {**execution, "deduplicated": True}
                assert worker_loop_once(session_factory) is None
                assert child_modes == ["preview", "preview", "execute"] and len(synthetic_cancellations) == 1

                payload = client.get(f"/api/target-cleanup/batches/{selected['batch_id']}").json()
                assert payload["execute_status"] == "completed" and payload["counts"]["submitted"] == 1
                assert payload["jobs"]["execute"]["status"] == "succeeded" and not payload["can_execute"]
                assert_bootstrap(client.get(selected["batch_url"]), selected["batch_id"])
                for download_name in ("scan_csv", "candidates_csv", "results_csv", "backup_csv"):
                    download_url = payload["downloads"][download_name]
                    filename = parse_qs(urlsplit(download_url).query)["filename"][0]
                    assert filename.startswith("target_cleanup/")
                    response = client.get(download_url)
                    assert response.status_code == 200
                    assert response.content == (user_root / "exports" / filename).read_bytes()
                assert client.get("/api/exports/download", params={
                    "filename": "target_cleanup/" + selected["batch_id"] + "_snapshot.json",
                }).status_code == 404
                assert client.get("/api/exports/download", params={"filename": "../../config.toml"}).status_code == 404
                assert client.get("/api/target-cleanup/batches").json()["total"] == 2
                with session_factory() as session:
                    batch = session.get(TargetCleanupBatch, selected["batch_id"])
                    assert batch.execute_job_id == execution["job_id"]
                    summary = json.loads(session.get(Job, execution["job_id"]).result_summary)
                    assert "not platform-final" in summary["message"]
        finally:
            engine.dispose()
        forbidden.assert_not_called()

    loaded_module_paths = {}
    for name, module in tuple(sys.modules.items()):
        if name == "cleanup_target_plans" or name.split(".")[0] in {"assistant", "lib", "scripts"}:
            module_file = getattr(module, "__file__", None)
            if module_file:
                module_path = Path(module_file).resolve()
                assert module_path.is_relative_to(application_root), name
                loaded_module_paths[name] = module_path.relative_to(application_root).as_posix()
            for package_path in getattr(module, "__path__", ()):
                assert Path(package_path).resolve().is_relative_to(application_root), name
    assert not any(name == "zn_daren" or name.startswith("zn_daren.") for name in sys.modules)
    assert not blocked_events, blocked_events
    print(json.dumps({
        "scope": "offline-host-interpreter-code-resource-smoke",
        "revision": head_revision.revision, "preview_months": [2, 4],
        "synthetic_cancellations": len(synthetic_cancellations), "child_modes": child_modes,
        "modules": loaded_module_paths,
    }, sort_keys=True))


def test_registered_cleanup_resources_load_and_run_offline_without_source_checkout(tmp_path):
    """Registered-resource integration only; no native/private-runtime proof."""
    bundle_root = tmp_path / "resources"
    selected_resources = resources.copy_application_resources(PROJECT_ROOT, bundle_root)
    application_root = bundle_root / "app"
    original_payload = {path: hashlib.sha256((application_root / path).read_bytes()).hexdigest()
                        for path in selected_resources}
    outside_cwd = tmp_path / "outside cwd"
    outside_cwd.mkdir()
    environment = smoke.isolated_environment(tmp_path, 8765, os.environ)
    program = inspect.getsource(run_isolated_cleanup_resource_smoke) + "\nrun_isolated_cleanup_resource_smoke()\n"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(tmp_path), str(PROJECT_ROOT)],
        cwd=outside_cwd, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, (result.stderr + result.stdout)[-1500:]
    report = json.loads(result.stdout)
    assert report["scope"] == "offline-host-interpreter-code-resource-smoke"
    assert report["revision"] == "0007_target_cleanup" and report["preview_months"] == [2, 4]
    assert report["child_modes"] == ["preview", "preview", "execute"] and report["synthetic_cancellations"] == 1
    assert {
        "assistant.api.target_cleanup", "assistant.services.target_cleanup_service",
        "assistant.jobs.handlers.target_cleanup", "cleanup_target_plans",
        "lib.target_invitation_dom", "lib.target_invitation_navigation", "lib.target_plan_cleanup",
    } <= report["modules"].keys()
    assert {path: hashlib.sha256((application_root / path).read_bytes()).hexdigest()
            for path in selected_resources} == original_payload
    assert {path.relative_to(application_root).as_posix() for path in application_root.rglob("*") if path.is_file()} == (
        set(selected_resources) | {"config.toml.example", ".env.example"}
    )
    assert any((tmp_path / "synthetic user data/exports/target_cleanup").glob("*_results.csv"))
