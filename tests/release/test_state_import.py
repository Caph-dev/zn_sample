"""Synthetic same-machine state only: no operator data or remote operations."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant import paths  # noqa: E402
from assistant.services import local_state_import as importer  # noqa: E402

SYNTHETIC_SECRET = "synthetic-only-secret-008"
SYNTHETIC_PROOF_KEY = b"synthetic-proof-key-008".ljust(32, b"_")
CONFIGURATION = '''[stores]
default_store_id = "store-one"
prepare_store_id = "store-two"
[feishu]
app_id = "synthetic-read-app"
app_secret = "synthetic-only-secret-008"
[feishu.bitable]
app_id = "synthetic-write-app"
app_secret = "synthetic-only-secret-008"
[content_review]
cache_dir = "exports/content_review_cache"
'''
ENVIRONMENT = "LLM_API_KEY=synthetic-only-secret-008\nTIKHUB_API_KEY=synthetic-only-secret-008\nARK_API_KEY=synthetic-only-secret-008\n"


@pytest.fixture
def state(tmp_path):
    source_root = tmp_path / "legacy project"
    source_root.mkdir()
    (source_root / "config.toml").write_text(CONFIGURATION, encoding="utf-8")
    (source_root / ".env").write_text(ENVIRONMENT, encoding="utf-8")
    export_root = source_root / "exports"
    export_root.mkdir()
    (export_root / "sample_intro_old.json").write_text(json.dumps([{
        "creator_id": "creator-one", "product_id": "product-one", "apply_id": "application-one",
        "send_status": "sent", "send_postcheck": "unknown", "message": "synthetic private chat",
    }]), encoding="utf-8")
    cache_root = export_root / "content_review_cache"
    cache_root.mkdir()
    assert len(SYNTHETIC_PROOF_KEY) == 32, "Production proof keys require exactly 32 bytes"
    (cache_root / ".proof-key").write_bytes(SYNTHETIC_PROOF_KEY)
    (cache_root / "proof.json").write_text(json.dumps({"path": str(cache_root / "frame.jpg")}), encoding="utf-8")
    (cache_root / "frame.jpg").write_bytes(b"synthetic-media")
    return source_root, tmp_path / "user data"


def run_import(state, *, apply=False, **arguments):
    source_root, target_root = state
    return importer.import_local_state(
        source_root, target_root=target_root, apply=apply, yes=apply,
        process_inspector=lambda: [], lock_first_startup_ready=True, **arguments,
    )


def file_tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def archive_root(target_root):
    registry = json.loads((target_root / "config/historical-exports.json").read_text())
    return Path(registry["archives"][0]["root"])


def test_dry_run_has_no_files_directories_database_or_secret_output(state, capsys, caplog):
    source_root, target_root = state
    before = file_tree(source_root)
    report = run_import(state)
    assert report.result == "planned" and report.exit_code == 0
    assert not target_root.exists()
    assert file_tree(source_root) == before
    serialized = json.dumps(report.as_dict()) + capsys.readouterr().out + caplog.text
    assert SYNTHETIC_SECRET not in serialized
    assert "synthetic private chat" not in serialized
    assert SYNTHETIC_PROOF_KEY.decode("ascii") not in serialized


def test_apply_byte_preservation_permissions_latest_isolation_and_idempotence(state, monkeypatch):
    source_root, target_root = state
    before = file_tree(source_root)
    report = run_import(state, apply=True)
    assert report.result == "imported" and report.exit_code == 0
    archive = archive_root(target_root)
    for relative_path, content in before.items():
        assert (archive / relative_path).read_bytes() == content
    assert (target_root / "config/config.toml").read_bytes() == before["config.toml"]
    assert (target_root / "config/.env").read_bytes() == before[".env"]
    assert not (target_root / "assistant.sqlite3").exists()
    assert not (target_root / "exports").exists()
    assert file_tree(source_root) == before
    target_before = file_tree(target_root)
    assert run_import(state, apply=True).result == "unchanged"
    assert run_import(state).result == "unchanged"
    assert file_tree(target_root) == target_before
    monkeypatch.setattr(paths, "configuration_dir", lambda: target_root / "config")
    assert paths.historical_export_dirs() == (archive / "exports",)
    if os.name != "nt":
        for directory in (target_root / "config", target_root / "legacy", archive):
            assert directory.stat().st_mode & 0o777 == 0o700
        for filename in (target_root / "config/.env", archive / "exports/content_review_cache/.proof-key"):
            assert filename.stat().st_mode & 0o777 == 0o600


def test_nonidentical_target_is_never_overwritten(state):
    _, target_root = state
    (target_root / "config").mkdir(parents=True)
    destination = target_root / "config/config.toml"
    destination.write_text("existing operator configuration")
    before = file_tree(target_root)
    report = run_import(state, apply=True)
    assert report.result == "conflict" and report.exit_code == 2
    assert file_tree(target_root) == before


def test_same_source_changed_after_completed_import_is_conflict(state):
    source_root, target_root = state
    assert run_import(state, apply=True).exit_code == 0
    before = file_tree(target_root)
    (source_root / "exports/new.json").write_text("[]")
    report = run_import(state, apply=True)
    assert report.exit_code == 2
    assert "archive-content-conflict" in report.issues
    assert file_tree(target_root) == before


@pytest.mark.parametrize("target_kind", ["same", "inside", "ancestor"])
def test_overlapping_source_and_target_refused(state, target_kind):
    source_root, _ = state
    target_root = {"same": source_root, "inside": source_root / "data", "ancestor": source_root.parent}[target_kind]
    before = file_tree(source_root)
    report = importer.import_local_state(source_root, target_root=target_root)
    assert "overlapping-roots" in report.issues
    assert file_tree(source_root) == before


@pytest.mark.parametrize("link_kind", ["config", "directory", "target", "internal"])
def test_symlink_escape_and_internal_aliases_refused(state, link_kind):
    source_root, target_root = state
    if link_kind == "config":
        (source_root / "config.toml").unlink()
        (source_root / "config.toml").symlink_to(source_root.parent / "outside")
    elif link_kind == "directory":
        (source_root / "exports/escape").symlink_to(source_root.parent, target_is_directory=True)
    elif link_kind == "internal":
        (source_root / "exports/alias.json").symlink_to(source_root / "exports/sample_intro_old.json")
    else:
        target_root.mkdir()
        (target_root / "config").symlink_to(source_root, target_is_directory=True)
    report = run_import(state, apply=True)
    assert report.exit_code == 2
    assert "symlink-not-allowed" in report.issues


@pytest.mark.parametrize("configuration", [
    "PATH=synthetic-only-secret-008\n", "PYTHONPATH=synthetic-only-secret-008\n",
    "NODE_OPTIONS=synthetic-only-secret-008\n", "UNREVIEWED_TOKEN=synthetic-only-secret-008\n",
    "CONTENT_REVIEW_EXTERNAL_ENV_PATH=/must/not/be/read\n", "FFMPEG_PATH=/must/not/be/read\n",
    "CONTENT_REVIEW_CACHE_DIR=/must/not/be/read\n", "LLM_API_KEY=duplicate\n",
])
def test_dotenv_whitelist_and_machine_paths_block_without_leaking_values(state, configuration):
    source_root, target_root = state
    with (source_root / ".env").open("a") as output:
        output.write(configuration)
    report = run_import(state, apply=True)
    assert report.result == "blocked"
    assert not target_root.exists()
    assert SYNTHETIC_SECRET not in json.dumps(report.as_dict())
    assert "/must/not/be/read" not in json.dumps(report.as_dict())


@pytest.mark.parametrize("addition", [
    '\n[unknown]\nsecret = "synthetic-only-secret-008"\n',
    '\nexternal_env_path = "/not/read/external/.env"\n',
    '\nffmpeg_path = "/developer/ffmpeg"\n',
    '\nunknown_option = "synthetic-only-secret-008"\n',
])
def test_unknown_toml_fields_and_machine_paths_require_review(state, addition):
    source_root, target_root = state
    with (source_root / "config.toml").open("a") as output:
        output.write(addition)
    report = run_import(state, apply=True)
    assert report.exit_code == 2 and report.issues
    assert not target_root.exists()
    assert SYNTHETIC_SECRET not in json.dumps(report.as_dict())


@pytest.mark.parametrize("empty_location", ["root", "nested"])
def test_empty_configuration_does_not_hide_second_file_conflict(state, empty_location):
    source_root, target_root = state
    (source_root / "config").mkdir()
    (source_root / "config.toml").write_text("" if empty_location == "root" else CONFIGURATION)
    (source_root / "config/config.toml").write_text(CONFIGURATION if empty_location == "root" else "")
    report = run_import(state, apply=True)
    assert report.exit_code == 2
    assert "multiple-config-files-conflict" in report.issues
    assert not target_root.exists()


def test_missing_configuration_never_reports_success(state):
    source_root, target_root = state
    (source_root / "config.toml").unlink()
    (source_root / ".env").unlink()
    report = run_import(state)
    assert report.result == "blocked" and "LLM_API_KEY" in report.missing_fields
    assert not target_root.exists()


@pytest.mark.parametrize("pid", [os.getpid(), "untrusted", None])
def test_running_or_unknown_instance_blocks_without_cleaning_state(state, pid):
    _, target_root = state
    (target_root / "runtime").mkdir(parents=True)
    (target_root / "runtime/state.json").write_text(json.dumps({"pid": pid}))
    before = file_tree(target_root)
    report = run_import(state, apply=True)
    assert report.exit_code == 2
    assert file_tree(target_root) == before


def test_busy_lock_is_shared_with_console_and_is_not_reclaimed(state):
    from assistant.lifecycle import InstanceLock

    _, target_root = state
    lock = InstanceLock(target_root / "runtime/instance.lock")
    assert lock.acquire()
    try:
        before = file_tree(target_root)
        assert "instance-lock-busy" in run_import(state).issues
        assert file_tree(target_root) == before
        report = run_import(state, apply=True)
        assert "instance-lock-busy" in report.issues
        assert not (target_root / "config").exists()
    finally:
        lock.release()


@pytest.mark.parametrize("command_line", [
    '42 python "/other root/scripts/send_sample_intro.py" --execute --yes',
    "42 python -m scripts.sync_shipped_tracking",
    "42 python /other/scripts/launch_assistant.py",
])
def test_independent_entrypoints_block_and_command_lines_are_not_reported(monkeypatch, command_line):
    monkeypatch.setattr(importer.subprocess, "run", lambda *arguments, **keywords: SimpleNamespace(stdout=command_line))
    assert importer.inspect_local_processes() == ["independent-entrypoint-running"]


def test_process_inventory_failure_is_not_treated_as_idle(state, monkeypatch):
    def fail(*arguments, **keywords):
        raise OSError(SYNTHETIC_SECRET)

    monkeypatch.setattr(importer.subprocess, "run", fail)
    report = importer.import_local_state(state[0], target_root=state[1], apply=True, yes=True, lock_first_startup_ready=True)
    assert report.issues == ["process-state-unknown"]
    assert SYNTHETIC_SECRET not in json.dumps(report.as_dict())
    assert not state[1].exists()


@pytest.mark.parametrize("change", ["edit", "added-file"])
def test_source_changes_during_copy_never_publish_completion(state, monkeypatch, change):
    source_root, target_root = state
    copy_file = importer.shutil.copyfileobj
    changed = False

    def copy_then_change(original, output, **keywords):
        nonlocal changed
        copy_file(original, output, **keywords)
        if not changed:
            changed = True
            path = source_root / ("exports/new.json" if change == "added-file" else ".env")
            path.write_text("changed")

    monkeypatch.setattr(importer.shutil, "copyfileobj", copy_then_change)
    report = run_import(state, apply=True)
    assert "source-changing" in report.issues
    assert not (target_root / "config/historical-exports.json").exists()
    assert not list(target_root.glob(".state-import-*"))


def test_public_apply_requires_confirmation_and_009_startup_contract(state):
    source_root, target_root = state
    report = importer.import_local_state(source_root, target_root=target_root, apply=True)
    assert report.issues == ["apply-requires-yes"]
    report = importer.import_local_state(source_root, target_root=target_root, apply=True, yes=True, process_inspector=lambda: [])
    assert report.issues == ["release-lifecycle-not-ready-009"]
    assert not target_root.exists()


def test_size_and_space_limits_are_fail_closed(state, monkeypatch):
    monkeypatch.setattr(importer, "MAX_CONFIG_BYTES", 8)
    assert run_import(state).issues == ["file-size-limit"]
    monkeypatch.setattr(importer, "MAX_CONFIG_BYTES", 1024 * 1024)
    monkeypatch.setattr(importer.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    assert run_import(state).issues == ["insufficient-disk-space"]
    assert not state[1].exists()


@pytest.mark.parametrize("job_status,error_code", [
    ("pending", ""), ("running", ""), ("failed", "write-uncertain"), ("interrupted", ""),
])
def test_database_active_or_uncertain_jobs_are_not_modified(state, job_status, error_code):
    _, target_root = state
    target_root.mkdir()
    database = target_root / "assistant.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (status TEXT, error_code TEXT)")
        connection.execute("INSERT INTO jobs VALUES (?, ?)", (job_status, error_code))
    before = database.read_bytes()
    report = run_import(state, apply=True)
    assert report.exit_code == 2 and report.issues[0].startswith("database-")
    assert database.read_bytes() == before
    assert not (target_root / "config").exists()


def test_sqlite_backup_includes_wal_without_moving_database_or_changing_rows(state):
    _, target_root = state
    target_root.mkdir()
    database = target_root / "assistant.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE preserved (value TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('unchanged business row')")
        connection.commit()
        wal_before = Path(str(database) + "-wal").read_bytes()
        tree_before = file_tree(target_root)
        report = run_import(state)
        assert report.issues == ["database-wal-needs-offline-check"]
        assert file_tree(target_root) == tree_before
        assert run_import(state, apply=True).result == "imported"
        backups = list((target_root / "backups").rglob("*.sqlite3"))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute("SELECT * FROM preserved").fetchall() == [("unchanged business row",)]
        assert connection.execute("SELECT * FROM preserved").fetchall() == [("unchanged business row",)]
        assert Path(str(database) + "-wal").read_bytes() == wal_before
    finally:
        connection.close()


def test_mid_publish_io_failure_preserves_existing_files_and_retry_is_safe(state, monkeypatch):
    _, target_root = state
    (target_root / "config").mkdir(parents=True)
    existing = target_root / "config/.env"
    existing.write_text(ENVIRONMENT)
    original_publish = importer._publish_file
    calls = 0

    def fail_after_first(staged, destination, root):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(SYNTHETIC_SECRET)
        return original_publish(staged, destination, root)

    monkeypatch.setattr(importer, "_publish_file", fail_after_first)
    report = run_import(state, apply=True)
    assert report.exit_code == 1 and report.result == "blocked"
    assert existing.read_text() == ENVIRONMENT
    assert not (target_root / "config/historical-exports.json").exists()
    assert not list(target_root.glob(".state-import-*"))
    assert SYNTHETIC_SECRET not in json.dumps(report.as_dict())
    monkeypatch.setattr(importer, "_publish_file", original_publish)
    assert run_import(state, apply=True).result == "imported"


def test_archive_manifest_contains_only_index_and_digests_not_credentials(state):
    source_root, target_root = state
    assert run_import(state, apply=True).exit_code == 0
    manifest = json.loads((archive_root(target_root) / "import-manifest.json").read_text())
    serialized = json.dumps(manifest)
    assert SYNTHETIC_SECRET not in serialized and "synthetic private chat" not in serialized
    for entry in manifest["files"]:
        assert entry["sha256"] == hashlib.sha256((source_root / entry["path"]).read_bytes()).hexdigest()


def test_cli_dry_run_is_standard_library_only_and_redacted(state, tmp_path):
    source_root, _ = state
    isolated_home = tmp_path / "synthetic home"
    isolated_home.mkdir()
    before = file_tree(isolated_home)
    environment = dict(os.environ, HOME=str(isolated_home), LOCALAPPDATA=str(isolated_home))
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(PROJECT_ROOT / "setup/release/import_legacy_state.py"), "--source", str(source_root)],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["result"] == "planned"
    assert file_tree(isolated_home) == before
    assert SYNTHETIC_SECRET not in completed.stdout + completed.stderr


def test_windows_existing_lock_is_not_reclaimed_and_permissions_use_acl_inheritance(state, monkeypatch):
    _, target_root = state
    (target_root / "runtime").mkdir(parents=True)
    (target_root / "runtime/instance.lock").write_text("unknown-owner")
    monkeypatch.setattr(importer, "sys", SimpleNamespace(platform="win32"))
    report = run_import(state, apply=True)
    assert "instance-lock-busy" in report.issues
    assert (target_root / "runtime/instance.lock").read_text() == "unknown-owner"
    monkeypatch.setattr(importer, "os", SimpleNamespace(name="nt"))

    def forbidden_chmod(*arguments, **keywords):
        raise AssertionError("chmod is not a Windows ACL")

    monkeypatch.setattr(Path, "chmod", forbidden_chmod)
    importer._private_directory(target_root / "acl-inherited")


def test_unreadable_export_subtree_is_not_silently_omitted(state, monkeypatch):
    def failing_walk(root, *, onerror, **keywords):
        onerror(PermissionError(SYNTHETIC_SECRET))
        yield root, [], []

    monkeypatch.setattr(importer.os, "walk", failing_walk)
    report = run_import(state)
    assert report.exit_code == 1 and report.result == "blocked"
    assert not state[1].exists()
    assert SYNTHETIC_SECRET not in json.dumps(report.as_dict())


def test_real_hmac_and_reconciliation_manifest_are_not_rewritten_by_archiving(state):
    from scripts.lib import creator_video_review as review, export_util
    from scripts.lib.tiktok_creator_videos import ReviewUnavailable

    source_root, target_root = state
    cache = source_root / "exports/content_review_cache"
    proof = {"handle": "synthetic-creator", "path": str(cache / "frame.jpg")}
    evidence = review._write_proof(proof, cache, cache)
    proof_bytes = evidence.read_bytes()
    envelope = json.loads(proof_bytes)
    original_key = review._signing_key(cache)
    export = source_root / "exports/sample_screen_original.json"
    export.write_text("[]")
    manifest_path = export_util.write_reconciliation_manifest([], out_prefix=export.with_suffix(""), stage="screen", store_id="synthetic-store")
    manifest_bytes = manifest_path.read_bytes()
    assert export_util.load_reconciliation_manifest_for_export(export) is not None
    assert run_import(state, apply=True).result == "imported"
    archive = archive_root(target_root)
    copied_cache = archive / "exports/content_review_cache"
    assert (copied_cache / evidence.name).read_bytes() == proof_bytes == evidence.read_bytes()
    assert review._signing_key(copied_cache) == original_key
    signature = hmac.new(original_key, review._canonical(envelope["proof"]), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, envelope["hmac_sha256"])
    assert manifest_path.read_bytes() == manifest_bytes == (archive / manifest_path.relative_to(source_root)).read_bytes()
    assert export_util.load_reconciliation_manifest_for_export(export) is not None
    with pytest.raises(export_util.ReconciliationManifestError, match="output.json_path"):
        export_util.load_reconciliation_manifest_for_export(archive / export.relative_to(source_root))
    with pytest.raises(ReviewUnavailable, match="evidence_path_outside_cache"):
        review._safe_path(copied_cache, str(evidence))


@pytest.mark.parametrize("schema,statement", [
    ("CREATE TABLE followup_tasks (send_result TEXT)", "INSERT INTO followup_tasks VALUES ('send-unknown')"),
    ("CREATE TABLE auto_approval_executions (status TEXT, error_code TEXT)", "INSERT INTO auto_approval_executions VALUES ('completed','write-uncertain')"),
    ("CREATE TABLE auto_approval_execution_items (approve_status TEXT, feishu_relation_status TEXT, platform_confirmation_status TEXT)",
     "INSERT INTO auto_approval_execution_items VALUES ('approval-unknown','','')"),
])
def test_uncertain_write_checkpoints_block_without_repair_or_replay(state, schema, statement):
    _, target_root = state
    target_root.mkdir()
    database = target_root / "assistant.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(schema)
        connection.execute(statement)
    before = database.read_bytes()
    report = run_import(state, apply=True)
    assert report.issues == ["database-uncertain-writes"]
    assert database.read_bytes() == before
    assert not (target_root / "config").exists()
