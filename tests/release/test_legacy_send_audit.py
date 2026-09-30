"""Release history must fail closed without any platform/Feishu calls."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from test_state_import import archive_root, run_import, state  # noqa: F401

from assistant import paths


@pytest.fixture
def audit(state, monkeypatch):
    _, target_root = state
    assert run_import(state, apply=True).exit_code == 0
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    script = importlib.import_module("send_sample_intro")
    monkeypatch.setattr(paths, "user_data_dir", lambda: target_root)
    monkeypatch.setattr(paths, "configuration_dir", lambda: target_root / "config")
    monkeypatch.setattr(paths, "is_packaged_distribution", lambda: True)
    monkeypatch.setattr(script, "is_packaged_distribution", lambda: True)
    monkeypatch.setattr(script, "exports_dir", lambda **keywords: target_root / "exports")
    monkeypatch.setattr(script, "load_dotenv", lambda: None)
    monkeypatch.setattr(script, "configure_logging", lambda **keywords: None)
    return script, target_root, archive_root(target_root)


def test_sent_history_union_preserves_product_and_application_keys(audit):
    script, target_root, _ = audit
    current_root = target_root / "exports"
    current_root.mkdir()
    (current_root / "sample_intro_current.json").write_text(json.dumps([{
        "creator_id": "creator-one", "product_id": "product-two", "send_status": "sent",
    }]))
    sent_keys, apply_ids = script._load_sent_intro_audit()
    assert sent_keys == {("creator-one", "product-one"), ("creator-one", "product-two")}
    assert apply_ids == {"application-one"}
    assert ("creator-one", "product-three") not in sent_keys


@pytest.mark.parametrize("damage", ["missing-file", "changed-bytes", "missing-manifest", "changed-manifest", "missing-registry", "extra-file", "symlink"])
def test_history_damage_blocks_before_any_platform_access(audit, monkeypatch, damage, caplog):
    script, target_root, archive = audit
    intro = archive / "exports/sample_intro_old.json"
    if damage == "missing-file":
        intro.unlink()
    elif damage == "changed-bytes":
        intro.write_text("[]")
    elif damage == "missing-manifest":
        (archive / "import-manifest.json").unlink()
    elif damage == "changed-manifest":
        (archive / "import-manifest.json").write_text("{}")
    elif damage == "missing-registry":
        (target_root / "config/historical-exports.json").unlink()
    elif damage == "extra-file":
        (archive / "exports/sample_intro_injected.json").write_text("[]")
    else:
        intro.unlink()
        intro.symlink_to(target_root / "nonexistent")

    def forbidden(*arguments, **keywords):
        raise AssertionError("No platform or Feishu access is allowed")

    for function_name in ("default_scan_store", "resolve_store_id", "_detect_from_detail", "_open_conversation",
                          "send_message_via_sdk", "fill_or_send_message", "get_bitable_access_token"):
        monkeypatch.setattr(script, function_name, forbidden)
    monkeypatch.setattr(sys, "argv", ["send_sample_intro.py", "--creator-id", "creator-one", "--execute", "--yes", "--write-feishu"])
    assert script.main() == 2
    assert "release-history-invalid" in caplog.text
    assert "synthetic private chat" not in caplog.text


@pytest.mark.parametrize("body", [
    '{"bad": "synthetic private chat"}', '"scalar"', '[null]', '{invalid',
    '[{}]', '[{"send_status":"sent"}]', '[{"send_status":"send-unknown"}]',
    '[{"send_status":"unknown"}]', '[{"send_status":"future-status"}]',
])
def test_current_release_audit_bad_json_or_shape_is_not_ignored(audit, body):
    script, target_root, _ = audit
    current_root = target_root / "exports"
    current_root.mkdir()
    (current_root / "sample_intro_bad.json").write_text(body)
    with pytest.raises(paths.ReleasePathError, match="release-history-invalid"):
        script._load_sent_intro_audit()


def test_source_mode_still_ignores_invalid_current_audit(audit, monkeypatch):
    script, target_root, _ = audit
    current_root = target_root / "exports"
    current_root.mkdir()
    (current_root / "sample_intro_bad.json").write_text("{invalid")
    monkeypatch.setattr(script, "is_packaged_distribution", lambda: False)
    sent_keys, _ = script._load_sent_intro_audit()
    assert ("creator-one", "product-one") in sent_keys


def test_release_v1_unverified_history_needs_review_not_silent_upgrade(audit):
    script, target_root, archive = audit
    (target_root / "config/historical-exports.json").write_text(json.dumps({
        "schema_version": 1, "directories": [str(archive / "exports")],
    }))
    with pytest.raises(paths.ReleasePathError, match="release-history-invalid"):
        script._load_sent_intro_audit()


def test_sent_with_unknown_postcheck_is_not_retried_on_new_export(audit, monkeypatch):
    script, target_root, _ = audit
    input_export = target_root / "new-targets.json"
    input_export.write_text(json.dumps([{
        "creator_id": "creator-one", "creator_name": "synthetic-creator", "product_id": "product-one",
        "apply_id": "new-application", "eligible": True,
    }]))
    monkeypatch.setattr(script, "resolve_from_export_arg", lambda argument: input_export)
    monkeypatch.setattr(script, "default_scan_store", lambda **keywords: {"store_id": "synthetic-store"})
    monkeypatch.setattr(script, "resolve_store_id", lambda **keywords: "synthetic-store")
    monkeypatch.setattr(script, "_detect_from_detail", lambda *arguments, **keywords: {"ok": True, "lang": "en", "feishu_lang": "English"})

    def forbidden(*arguments, **keywords):
        raise AssertionError("Sent history must never open a conversation or send")

    monkeypatch.setattr(script, "_open_conversation", forbidden)
    monkeypatch.setattr(script, "send_message_via_sdk", forbidden)
    monkeypatch.setattr(script, "fill_or_send_message", forbidden)
    observed = []

    def report_results(rows, prefix, **keywords):
        observed.extend(rows)
        return {}

    monkeypatch.setattr(script, "write_generic_reports", report_results)
    monkeypatch.setattr(sys, "argv", ["send_sample_intro.py", "--from-export", "--execute", "--yes"])
    assert script.main() == 0
    assert observed[0]["send_status"] == "already-sent"


@pytest.mark.parametrize("change", ["changed-bytes", "deleted-file"])
def test_history_change_between_verification_and_actual_read_is_rejected(audit, monkeypatch, change):
    script, _, archive = audit
    intro = archive / "exports/sample_intro_old.json"
    read_directories = script.historical_export_dirs

    def verify_then_change():
        directories = read_directories()
        if change == "changed-bytes":
            intro.write_text("[]")
        else:
            intro.unlink()
        return directories

    monkeypatch.setattr(script, "historical_export_dirs", verify_then_change)
    with pytest.raises(paths.ReleasePathError, match="release-history-invalid"):
        script._load_sent_intro_audit()


def test_incomplete_unregistered_archive_does_not_erase_old_dedupe(audit):
    script, target_root, _ = audit
    (target_root / "legacy/incomplete-import").mkdir()
    with pytest.raises(paths.ReleasePathError, match="release-history-invalid"):
        script._load_sent_intro_audit()
