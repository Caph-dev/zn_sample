"""Offline resource and launcher tests; never start a business worker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from setup.release import build_bundle, resources, runtime_artifacts

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_checkout_has_the_complete_registered_resource_contract():
    selected = resources.collect_application_resources(PROJECT_ROOT)
    assert set(resources.FIXED_RESOURCES) <= set(selected)
    assert "assistant/web/static/console/console.html" in selected
    assert "assistant/web/static/auto-approval/index.html" in selected
    assert all(not path.endswith((".pyc", ".pyo")) and "__pycache__" not in path for path in selected)


def write_resource(root: Path, relative_path: str, content: str = "resource\n") -> Path:
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return destination


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    for relative_path in resources.FIXED_RESOURCES:
        write_resource(source, relative_path)
    for relative_directory, document_name in resources.FRONTEND_BUILDS.items():
        for required in (document_name, "assets/index.js", "assets/index.css", "assets/lazy.js"):
            write_resource(source, f"{relative_directory}/{required}")
    for page_name, build_name in (("console", "console"), ("auto_approval", "auto-approval")):
        write_resource(
            source, f"assistant/web/templates/{page_name}.html",
            f'<link href="/static/{build_name}/assets/index.css">\n'
            '<link href="/static/geist-theme.css">\n',
        )
    write_resource(source, "pyproject.toml", '[project]\nversion = "1.2.3"\n')
    write_resource(source, "uv.lock")
    for template_name in ("macos.command.in", "windows.cmd.in"):
        template_path = f"setup/release/launchers/{template_name}"
        write_resource(source, template_path, (PROJECT_ROOT / template_path).read_text(encoding="utf-8"))
    return source


def test_complete_application_payload_and_empty_python_packages(source_root: Path, tmp_path: Path):
    (source_root / "assistant/__init__.py").write_text("", encoding="utf-8")
    bundle_root = tmp_path / "bundle"
    selected = resources.copy_application_resources(source_root, bundle_root)
    assert set(resources.FIXED_RESOURCES) <= set(selected)
    assert len(selected) == len(set(selected))
    for relative_path in selected:
        assert (bundle_root / "app" / relative_path).read_bytes() == (source_root / relative_path).read_bytes()
    for build_name in resources.FRONTEND_BUILDS:
        assert (bundle_root / "app" / build_name / "assets/lazy.js").is_file()
    assert (bundle_root / "app" / resources.BUSINESS_ATTACHMENT).is_file()


@pytest.mark.parametrize("excluded_path", [
    "config.toml", ".env", "config.toml.example", ".env.example",
    "assistant/config.toml", "assistant/.env", "assistant/private_credentials.py",
    "assistant/assistant.sqlite3", "assistant/assistant.sqlite3-wal",
    "assistant/assistant.sqlite3-shm", "assistant/web/static/.proof-key",
    "exports/content_review_cache/result.json", "exports/screen.json", "logs/app.log",
    "config/external-cli.json", "authorization.json", ".git/config", "tests/test_secret.py",
    "plans/secret.md", "node_modules/private/index.js", "scripts/capture_logistics_fixture.py",
    "scripts/recover_auto_approval_state.py", "assistant/__pycache__/app.pyc",
])
def test_sensitive_and_development_files_are_not_copied(source_root: Path, tmp_path: Path, excluded_path: str):
    secret_marker = "synthetic-secret-must-not-be-distributed"
    write_resource(source_root, excluded_path, secret_marker)
    bundle_root = tmp_path / "bundle"
    resources.copy_application_resources(source_root, bundle_root)
    assert secret_marker not in "".join(
        path.read_text(encoding="utf-8") for path in (bundle_root / "app").rglob("*") if path.is_file()
    )


@pytest.mark.parametrize("required_path", [
    "assistant/app.py", "assistant/services/release_lifecycle.py", "scripts/release_launcher.py",
    "scripts/launch_sample.py", "scripts/send_followup_message.py", "scripts/lib/operator_launch.py",
    "assistant/database/alembic.ini", "assistant/database/migrations/versions/0001_initial.py",
    "assistant/web/templates/_task_panel.html", "assistant/web/static/geist-theme.css",
    "assistant/web/static/fonts/Geist.woff2", "assistant/web/static/fonts/GeistMono.woff2",
    "assistant/web/static/console/assets/index.js", "assistant/web/static/console/assets/index.css",
    "assistant/web/static/auto-approval/assets/index.js", "assistant/web/static/auto-approval/assets/index.css",
    "setup/release/runtime_artifacts.py", "setup/install_deps.py", resources.BUSINESS_ATTACHMENT,
])
def test_missing_required_resources_fail_before_copy(source_root: Path, tmp_path: Path, required_path: str):
    (source_root / required_path).unlink()
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="application-resource-required"):
        resources.copy_application_resources(source_root, tmp_path / "bundle")
    assert not (tmp_path / "bundle/app").exists()


def test_generated_templates_have_no_customer_ids_or_secrets(source_root: Path, tmp_path: Path):
    resources.copy_application_resources(source_root, tmp_path / "bundle")
    template = (tmp_path / "bundle/app/config.toml.example").read_text(encoding="utf-8")
    config = tomllib.loads(template)
    assert all(value == "" for value in config["stores"].values())
    assert config["content_review"]["enabled"] is True
    assert all(value == "" for value in config["feishu"]["bitable"].values())
    assert config["feishu"]["app_secret"] == ""
    assert "27437742526069" not in template and "27506607043054" not in template
    assert "LLM_API_KEY=\n" in (tmp_path / "bundle/app/.env.example").read_text(encoding="utf-8")


@pytest.mark.parametrize("link_kind", ["file-outside", "file-inside", "directory", "frontend-directory"])
def test_application_symlinks_are_rejected(source_root: Path, tmp_path: Path, link_kind: str):
    if link_kind == "directory":
        original = source_root / "scripts/lib"
        moved = tmp_path / "libraries"
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
    elif link_kind == "frontend-directory":
        original = source_root / "assistant/web/static/console/assets"
        moved = tmp_path / "assets"
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
    else:
        original = source_root / "scripts/release_launcher.py"
        original.unlink()
        target = source_root / "assistant/app.py" if link_kind == "file-inside" else write_resource(tmp_path, "outside.py")
        original.symlink_to(target)
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="symlink"):
        resources.copy_application_resources(source_root, tmp_path / "bundle")


@pytest.mark.parametrize("extra_name", ["secrets.json", "index.js.map", ".env", ".private.js", "test.py"])
def test_unexpected_frontend_build_files_fail_closed(source_root: Path, extra_name: str):
    write_resource(source_root, f"assistant/web/static/console/assets/{extra_name}")
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="unexpected-frontend"):
        resources.collect_application_resources(source_root)


def test_theme_loading_order_is_checked(source_root: Path):
    write_resource(source_root, "assistant/web/templates/console.html",
                   '<link href="/static/geist-theme.css"><link href="/static/console/assets/index.css">')
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="theme-order"):
        resources.collect_application_resources(source_root)


@pytest.mark.parametrize("target_name", ["macos-arm64", "windows-x64"])
def test_platform_launchers_use_only_fixed_isolated_commands(source_root: Path, tmp_path: Path, target_name: str):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    target = runtime_artifacts.get_target(target_name)
    resources.write_platform_launchers(source_root, bundle_root, target_name, target["python_executable"])
    suffix = ".command" if target_name == "macos-arm64" else ".cmd"
    for launcher_name, mode in resources.LAUNCHER_MODES.items():
        launcher = bundle_root / (launcher_name + suffix)
        content = launcher.read_text(encoding="utf-8")
        assert " -I -B " in content
        assert "release_launcher.py\" " + mode in content
        assert "@MODE@" not in content and "@PYTHON_EXECUTABLE@" not in content
        assert "%*" not in content and '"$@"' not in content
        assert "set \"PATH=" not in content and "export PATH=" not in content
        assert "--execute" not in content and "--yes" not in content
        if suffix == ".cmd":
            assert b"\n" not in launcher.read_bytes().replace(b"\r\n", b"")
        else:
            assert launcher.stat().st_mode & 0o111


@pytest.mark.skipif(sys.platform != "darwin", reason="native zsh launcher execution")
def test_macos_launcher_preserves_fixed_arguments_and_path_with_spaces(source_root: Path, tmp_path: Path):
    bundle_root = tmp_path / "\u4e2d\u6587 bundle with spaces"
    bundle_root.mkdir()
    write_resource(bundle_root, "runtime/python/bin/python3", '#!/bin/zsh -f\nprintf "%s\\n" "$@"\n').chmod(0o755)
    resources.write_platform_launchers(source_root, bundle_root, "macos-arm64", "runtime/python/bin/python3")
    result = subprocess.run([str(bundle_root / "2-Pipeline.command"), "ignored"], cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8", check=True)
    assert result.stdout.splitlines() == ["-I", "-B", str(bundle_root / "app/scripts/release_launcher.py"), "operator", "pipeline"]


def create_fake_runtime(bundle_root: Path, target_name: str, target: dict, arguments) -> None:
    for field in ("python_executable", "node_executable", "ffmpeg_executable"):
        write_resource(bundle_root, target[field], "synthetic-runtime\n")
    write_resource(bundle_root, "runtime/python/lib/python3.12/site-packages/example-1.0.dist-info/METADATA")
    manifest = build_bundle.create_manifest(bundle_root, target_name, target, {"source_changes": "none"})
    build_bundle.write_json(bundle_root / "release-manifest.json", manifest)


def test_bundle_assembly_preserves_private_runtime_and_full_manifest(source_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", source_root)
    monkeypatch.setattr(build_bundle, "build_runtime", create_fake_runtime)
    build_order = []
    monkeypatch.setattr(build_bundle, "build_static_assets", lambda *arguments: build_order.append("assets"))
    target = runtime_artifacts.get_target("macos-arm64")
    bundle_root = tmp_path / "output/bundle"
    manifest = build_bundle.build_application_bundle(bundle_root, "macos-arm64", target, argparse.Namespace(rebuild=False))
    assert build_order == ["assets"]
    assert manifest["scope"] == "browser-application-bundle"
    assert manifest["application_version"] == "1.2.3"
    assert manifest["target"] == "macos-arm64"
    assert manifest["executables"]["python_executable"] == target["python_executable"]
    entries = {entry["path"] for entry in manifest["files"]}
    assert "app/scripts/release_launcher.py" in entries and "Start.command" in entries
    assert "runtime/python/lib/python3.12/site-packages/example-1.0.dist-info/METADATA" in entries
    assert "release-manifest.json" not in entries
    assert str(source_root) not in json.dumps(manifest)
    assert build_bundle.verify_manifest(bundle_root, "macos-arm64") == manifest
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="bundle-exists"):
        build_bundle.build_application_bundle(bundle_root, "macos-arm64", target, argparse.Namespace(rebuild=False))
    build_bundle.build_application_bundle(bundle_root, "macos-arm64", target, argparse.Namespace(rebuild=True))
    assert len(list((bundle_root.parent.parent / ".previous").glob("bundle-*/payload/release-manifest.json"))) == 1


def test_private_node_build_uses_npm_js_and_complete_asset_entry(tmp_path: Path, monkeypatch):
    target = runtime_artifacts.get_target("windows-x64")
    write_resource(tmp_path, "runtime/node/node_modules/npm/bin/npm-cli.js")
    commands = []
    monkeypatch.setattr(build_bundle, "run_checked", lambda command, **kwargs: commands.append(command))
    build_bundle.build_static_assets(tmp_path, target)
    assert commands[0] == [str(tmp_path / target["node_executable"]), str(tmp_path / "runtime/node/node_modules/npm/bin/npm-cli.js"), "ci", "--ignore-scripts"]
    assert commands[1] == [str(tmp_path / target["node_executable"]), str(PROJECT_ROOT / "setup/release/build_static_assets.mjs"), "web"]


def test_bundle_subcommand_does_not_run_business_or_claim_smoke_acceptance(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", tmp_path)
    monkeypatch.setattr(runtime_artifacts, "require_native_host", lambda target: None)
    monkeypatch.setattr(build_bundle, "build_application_bundle", lambda *arguments: {
        "scope": "browser-application-bundle", "application_version": "1.2.3",
    })
    monkeypatch.setattr(build_bundle, "probe_runtime", lambda *arguments: pytest.fail("bundle must not probe external CLI"))
    original_environment = dict(os.environ)
    report, exit_code = build_bundle.execute(argparse.Namespace(command="bundle", target="macos-arm64"))
    assert exit_code == 0
    assert report["bundle"] == "build/release/macos-arm64/bundle"
    assert report["native_verified"] is False
    assert report["bundle_smoke_verified"] is False
    assert report["parent_environment_unchanged"] is True
    assert dict(os.environ) == original_environment


def test_manifest_rejects_absolute_or_escaping_symlinks(source_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", source_root)
    bundle_root = tmp_path / "bundle"
    write_resource(bundle_root, "runtime/data")
    (bundle_root / "runtime/unsafe").symlink_to(bundle_root / "runtime/data")
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="symlink-escape"):
        build_bundle.create_manifest(bundle_root, "macos-arm64", runtime_artifacts.get_target("macos-arm64"), {})


def test_manifest_preserves_contained_relative_runtime_links(source_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", source_root)
    bundle_root = tmp_path / "bundle"
    write_resource(bundle_root, "runtime/python/bin/python3.12")
    (bundle_root / "runtime/python/bin/python3").symlink_to("python3.12")
    manifest = build_bundle.create_manifest(bundle_root, "macos-arm64", runtime_artifacts.get_target("macos-arm64"), {})
    assert {"path": "runtime/python/bin/python3", "symlink": "python3.12"} in manifest["files"]


def test_manifest_preserves_contained_broken_python_runtime_links(source_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", source_root)
    bundle_root = tmp_path / "bundle"
    write_resource(bundle_root, "runtime/python/bin/python3.12")
    (bundle_root / "runtime/python/bin/idle3").symlink_to("idle3.12")
    manifest = build_bundle.create_manifest(bundle_root, "macos-arm64", runtime_artifacts.get_target("macos-arm64"), {})
    expected_entry = {"path": "runtime/python/bin/idle3", "symlink": "idle3.12"}
    assert expected_entry in manifest["files"]


def test_application_copy_cannot_write_through_destination_symlink(source_root: Path, tmp_path: Path):
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    bundle_root = tmp_path / "bundle"
    bundle_root.symlink_to(outside_root, target_is_directory=True)
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="destination-symlink"):
        resources.copy_application_resources(source_root, bundle_root)
    assert not list(outside_root.iterdir())


@pytest.mark.parametrize("python_path", ["/usr/bin/python3", "../python", "runtime/python/bin/python3;id", "C:/python.exe"])
def test_launcher_rejects_untrusted_interpreter_paths(source_root: Path, tmp_path: Path, python_path: str):
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="launcher-python-path-invalid"):
        resources.write_platform_launchers(source_root, tmp_path, "macos-arm64", python_path)


def test_failed_rebuild_preserves_existing_bundle(source_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", source_root)
    monkeypatch.setattr(build_bundle, "build_runtime", create_fake_runtime)
    monkeypatch.setattr(build_bundle, "build_static_assets", lambda *arguments: None)
    bundle_root = tmp_path / "output/bundle"
    target = runtime_artifacts.get_target("macos-arm64")
    build_bundle.build_application_bundle(bundle_root, "macos-arm64", target, argparse.Namespace(rebuild=False))
    previous_bytes = (bundle_root / "release-manifest.json").read_bytes()
    def fail_static_build(*arguments):
        raise runtime_artifacts.RuntimeBlocked("synthetic-static-build-failure")
    monkeypatch.setattr(build_bundle, "build_static_assets", fail_static_build)
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="synthetic-static"):
        build_bundle.build_application_bundle(bundle_root, "macos-arm64", target, argparse.Namespace(rebuild=True))
    assert (bundle_root / "release-manifest.json").read_bytes() == previous_bytes
    assert not (bundle_root.parent.parent / ".previous").exists()


def test_completed_bundle_does_not_break_006_runtime_proof_verification(source_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", source_root)
    monkeypatch.setattr(build_bundle, "build_runtime", create_fake_runtime)
    monkeypatch.setattr(build_bundle, "build_static_assets", lambda *arguments: None)
    target = runtime_artifacts.get_target("macos-arm64")
    runtime_proof_root = tmp_path / "macos-arm64"
    create_fake_runtime(runtime_proof_root, "macos-arm64", target, None)
    original_manifest = build_bundle.verify_manifest(runtime_proof_root, "macos-arm64")
    build_bundle.build_application_bundle(runtime_proof_root / "bundle", "macos-arm64", target, argparse.Namespace(rebuild=False))
    assert build_bundle.verify_manifest(runtime_proof_root, "macos-arm64") == original_manifest
    build_bundle.build_application_bundle(runtime_proof_root / "bundle", "macos-arm64", target, argparse.Namespace(rebuild=True))
    assert build_bundle.verify_manifest(runtime_proof_root, "macos-arm64") == original_manifest


def test_verified_runtime_copy_preserves_dist_info_and_broken_symlinks(source_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", source_root)
    target = runtime_artifacts.get_target("macos-arm64")
    runtime_root = tmp_path / "runtime-source"
    create_fake_runtime(runtime_root, "macos-arm64", target, None)
    (runtime_root / "runtime/python/bin/idle3").symlink_to("idle3.12")
    manifest = build_bundle.create_manifest(runtime_root, "macos-arm64", target, {"source_changes": "none"})
    build_bundle.write_json(runtime_root / "release-manifest.json", manifest)
    destination_root = tmp_path / "runtime-copy"
    copied_manifest = build_bundle.copy_verified_runtime(runtime_root, destination_root, "macos-arm64")
    assert copied_manifest == manifest
    assert (destination_root / "runtime/python/bin/idle3").is_symlink()
    assert (destination_root / "runtime/python/bin/idle3").readlink() == Path("idle3.12")
    assert build_bundle.verify_manifest(destination_root, "macos-arm64") == manifest


def test_application_manifest_rejects_unstripped_python_bytecode(source_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build_bundle, "ROOT", source_root)
    bundle_root = tmp_path / "bundle"
    write_resource(bundle_root, "runtime/python/lib/python3.12/example/__pycache__/module.cpython-312.pyc", "bytecode")
    manifest = build_bundle.create_manifest(
        bundle_root, "macos-arm64", runtime_artifacts.get_target("macos-arm64"), {},
        scope=resources.APPLICATION_SCOPE,
    )
    build_bundle.write_json(bundle_root / "release-manifest.json", manifest)
    with pytest.raises(runtime_artifacts.RuntimeBlocked, match="application-bytecode-forbidden"):
        build_bundle.verify_manifest(bundle_root, "macos-arm64")
