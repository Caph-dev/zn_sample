#!/usr/bin/env python3
"""Build private runtimes and complete directory bundles without running business.

CLI remains an explicit, technician-installed external prerequisite. Native
build tools are used only on the build machine; PATH is never changed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
from pathlib import Path

if __package__:
    from . import runtime_artifacts as artifacts
    from . import resources
else:
    import runtime_artifacts as artifacts
    import resources

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)
policy = artifacts.policy
RuntimeBlocked = artifacts.RuntimeBlocked

PYTHON_PROBE = r"""
import ctypes, hashlib, importlib, importlib.metadata, json, pathlib
import platform, sqlite3, ssl, subprocess, sys, zoneinfo
prefix = pathlib.Path(sys.prefix).resolve()
expected_version = sys.argv[1]
assert platform.python_version() == expected_version
assert pathlib.Path(sys.executable).resolve().is_relative_to(prefix)
assert sys.prefix == sys.base_prefix
modules = ("alembic", "fastapi", "httptools", "httpx", "itsdangerous", "jinja2",
           "pydantic", "multipart", "sqlalchemy", "starlette", "uvicorn",
           "watchfiles", "websockets", "certifi", "tzdata")
for module_name in modules:
    importlib.import_module(module_name)
if sys.platform != "win32":
    import uvloop
zoneinfo.reset_tzpath([])
assert zoneinfo.ZoneInfo("Asia/Shanghai").key == "Asia/Shanghai"
assert sqlite3.connect(":memory:").execute("select 1").fetchone() == (1,)
origins = {}
for module_name, module in tuple(sys.modules.items()):
    origin = getattr(module, "__file__", None)
    if origin and not origin.startswith("<"):
        origin_path = pathlib.Path(origin).resolve()
        assert origin_path.is_relative_to(prefix), "non-private-import"
        origins[module_name] = origin_path.relative_to(prefix).as_posix()
for search_path in sys.path:
    assert pathlib.Path(search_path).resolve().is_relative_to(prefix), "non-private-sys-path"
versions = {}
for distribution in importlib.metadata.distributions():
    assert distribution.read_text("direct_url.json") is None, "local-or-editable-install"
    versions[distribution.metadata["Name"]] = distribution.version
assert "pytest" not in versions
for path_file in prefix.rglob("*.pth"):
    for line in path_file.read_text().splitlines():
        assert not pathlib.Path(line).is_absolute(), "absolute-pth"
child = subprocess.run([sys.executable, "-I", "-B", "-c",
    "import json,sys; print(json.dumps({'version':sys.version_info[:3], 'prefix':sys.prefix}))"],
    capture_output=True, text=True, encoding="utf-8", shell=False, check=True)
child_data = json.loads(child.stdout)
assert child_data["prefix"] == sys.prefix
print(json.dumps({"version": platform.python_version(), "machine": platform.machine(),
    "versions": versions,
    "import_origins": {module_name: origins[module_name] for module_name in modules},
    "inspected_import_count": len(origins), "all_imports_private": True,
    "child_verified": True, "packaged_timezone_verified": True}))
"""


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr
    )


def run_checked(
    command: list[str],
    *,
    cwd: Path,
    label: str,
    timeout: int = 120,
    environment: dict | None = None,
    binary: bool = False,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment
            if environment is not None
            else artifacts.isolated_environment(),
            shell=False,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeBlocked(f"{label}-unavailable-or-timeout") from error
    if result.returncode:
        # Do not echo external CLI output, inherited configuration, or local paths.
        raise RuntimeBlocked(f"{label}-exit-{result.returncode}")
    if not binary:
        result.stdout = result.stdout.decode("utf-8", errors="replace")
        result.stderr = result.stderr.decode("utf-8", errors="replace")
    return result


def resolve_build_tool(explicit: str | None, default: str) -> str:
    discovered = explicit or shutil.which(default)
    if not discovered or not Path(discovered).is_file():
        raise RuntimeBlocked(f"build-tool-{default}-required")
    return Path(discovered).resolve().as_posix()


def export_production_requirements(uv_path: str, destination: Path) -> None:
    run_checked([uv_path, "lock", "--check"], cwd=ROOT, label="lock-check")
    run_checked(
        [
            uv_path,
            "export",
            "--frozen",
            "--no-dev",
            "--no-default-groups",
            "--no-emit-project",
            "--no-emit-local",
            "--no-header",
            "--no-annotate",
            "--format",
            "requirements.txt",
            "--output-file",
            str(destination),
        ],
        cwd=ROOT,
        label="production-export",
    )
    requirements = destination.read_text(encoding="utf-8")
    if any(token in requirements for token in ("file:", "-e ", str(ROOT), "pytest==")):
        raise RuntimeBlocked(
            "production-export-contains-local-or-development-dependencies"
        )


def unpack_runtime(
    artifact: dict, staging_root: Path, cache_root: Path, runtime_name: str
) -> None:
    filename = Path(
        urllib.parse.unquote(urllib.parse.urlsplit(artifact["url"]).path)
    ).name
    archive_path = artifacts.download_verified(artifact, cache_root / filename)
    extracted_root = staging_root / ("extract-" + runtime_name)
    artifacts.extract_verified_archive(archive_path, extracted_root)
    resource_root = extracted_root / artifact["archive_root"]
    if not resource_root.is_dir():
        raise RuntimeBlocked("upstream-resource-root-mismatch")
    resource_root.rename(staging_root / "runtime" / runtime_name)
    shutil.rmtree(extracted_root)


def build_ffmpeg(staging_root: Path, cache_root: Path, target: dict, arguments) -> dict:
    """Build an unmodified LGPL standalone executable; ship its exact source too."""
    artifact = policy.RELEASE_FFMPEG_SOURCE
    source_archive = artifacts.download_verified(
        artifact, cache_root / f"ffmpeg-{artifact['version']}.tar.xz"
    )
    source_root = staging_root / "ffmpeg-build"
    artifacts.extract_verified_archive(source_archive, source_root)
    source_directory = source_root / artifact["archive_root"]
    if target["system"] == "Darwin":
        shell_path = resolve_build_tool(arguments.ffmpeg_shell, "/bin/sh")
        compiler_path = resolve_build_tool(arguments.ffmpeg_cc, "/usr/bin/clang")
        make_path = resolve_build_tool(arguments.ffmpeg_make, "/usr/bin/make")
        target_arguments = [
            "--arch=aarch64",
            "--target-os=darwin",
            f"--extra-cflags=-mmacosx-version-min={target['minimum_os']}",
            f"--extra-ldflags=-mmacosx-version-min={target['minimum_os']}",
        ]
    else:
        # Windows recipe requires a native, already configured MSYS2/MinGW build
        # environment. No toolchain is installed or PATH repaired by this script.
        if not all(
            (arguments.ffmpeg_shell, arguments.ffmpeg_cc, arguments.ffmpeg_make)
        ):
            raise RuntimeBlocked("windows-native-msys2-build-tools-must-be-explicit")
        shell_path = resolve_build_tool(arguments.ffmpeg_shell, "sh")
        compiler_path = resolve_build_tool(arguments.ffmpeg_cc, "gcc")
        make_path = resolve_build_tool(arguments.ffmpeg_make, "make")
        target_arguments = [
            "--arch=x86_64",
            "--target-os=mingw32",
            "--extra-ldflags=-static -static-libgcc",
        ]
    configure_arguments = [
        *policy.RELEASE_FFMPEG_CONFIGURE,
        *target_arguments,
        f"--cc={compiler_path}",
        f"--host-cc={compiler_path}",
    ]
    logger.info("Building FFmpeg from checksum-verified source (no external codecs)")
    run_checked(
        [shell_path, "configure", *configure_arguments],
        cwd=source_directory,
        label="ffmpeg-configure",
        timeout=180,
    )
    run_checked(
        [make_path, "-j", str(min(os.cpu_count() or 1, 8)), "ffmpeg"],
        cwd=source_directory,
        label="ffmpeg-compile",
        timeout=1200,
    )
    runtime_directory = staging_root / "runtime" / "ffmpeg"
    runtime_directory.mkdir()
    executable_name = "ffmpeg.exe" if target["system"] == "Windows" else "ffmpeg"
    shutil.copy2(
        source_directory / executable_name, runtime_directory / executable_name
    )
    sources_directory = staging_root / "sources"
    sources_directory.mkdir()
    shutil.copy2(source_archive, sources_directory / source_archive.name)
    licenses_directory = staging_root / "licenses" / "ffmpeg"
    licenses_directory.mkdir(parents=True)
    for license_name in ("COPYING.LGPLv2.1", "LICENSE.md"):
        shutil.copy2(source_directory / license_name, licenses_directory / license_name)
    # configure embeds compiler locations in its report; do not put user paths
    # into the distributable provenance. The configured sources are unmodified.
    recipe = {
        "configure": list(policy.RELEASE_FFMPEG_CONFIGURE),
        "target_options": target_arguments,
        "compiler": "Apple clang"
        if target["system"] == "Darwin"
        else "native MinGW GCC",
        "source_changes": "none",
        "linkage": "static FFmpeg libraries; separate process",
    }
    write_json(licenses_directory / "build-recipe.json", recipe)
    shutil.rmtree(source_root)
    return recipe


def write_json(destination: Path, data: dict) -> None:
    destination.write_text(
        json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )


def create_manifest(
    bundle_root: Path, target_name: str, target: dict, recipe: dict,
    *, scope: str = "runtime-proof-not-full-application",
) -> dict:
    entries = []
    for path in sorted(bundle_root.rglob("*")):
        relative_path = path.relative_to(bundle_root).as_posix()
        if relative_path == "release-manifest.json":
            continue
        if path.is_symlink():
            link_value = os.readlink(path)
            if (
                Path(link_value).is_absolute()
                or "\\" in link_value
                or re.match(r"^[A-Za-z]:", link_value)
                or not path.resolve().is_relative_to(bundle_root.resolve())
            ):
                raise RuntimeBlocked("manifest-symlink-escape")
            entries.append({"path": relative_path, "symlink": os.readlink(path)})
        elif path.is_file():
            entries.append(
                {
                    "path": relative_path,
                    "sha256": artifacts.hash_file(path),
                    "size": path.stat().st_size,
                }
            )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "scope": scope,
        "application_version": project["project"]["version"],
        "target": target_name,
        "executables": {
            field: target[field]
            for field in ("python_executable", "node_executable", "ffmpeg_executable")
        },
        "sources": {
            "python": target["python"],
            "node": target["node"],
            "ffmpeg": policy.RELEASE_FFMPEG_SOURCE,
        },
        "uv_lock_sha256": artifacts.hash_file(ROOT / "uv.lock"),
        "external_prerequisites": [
            {
                **policy.RELEASE_EXTERNAL_CLI,
                "bundled": False,
                "installation": "technician; explicit absolute runner path",
            }
        ],
        "minimum_os_requirement": target["minimum_os"],
        "minimum_os_native_verified": False,
        "ffmpeg_recipe": recipe,
        "files": entries,
    }


def build_static_assets(runtime_root: Path, target: dict) -> None:
    """Use private Node and its npm JS entry; do not depend on shell shims."""
    node_path = runtime_root / target["node_executable"]
    npm_relative_path = (
        "runtime/node/node_modules/npm/bin/npm-cli.js"
        if target["system"] == "Windows"
        else "runtime/node/lib/node_modules/npm/bin/npm-cli.js"
    )
    npm_path = runtime_root / npm_relative_path
    if not npm_path.is_file():
        raise RuntimeBlocked("build-private-npm-entry-required")
    run_checked(
        [str(node_path), str(npm_path), "ci", "--ignore-scripts"],
        cwd=ROOT, label="frontend-locked-install", timeout=600,
    )
    run_checked(
        [str(node_path), str(ROOT / "setup/release/build_static_assets.mjs"), "web"],
        cwd=ROOT, label="frontend-complete-build", timeout=300,
    )


def copy_verified_runtime(runtime_root: Path, destination_root: Path, target_name: str) -> dict:
    """Reuse a verified 006 runtime without copying user data or old bundles."""
    manifest = verify_manifest(runtime_root, target_name, verify_nested_bundle=False)
    if manifest.get("scope") != "runtime-proof-not-full-application":
        raise RuntimeBlocked("runtime-proof-manifest-required")
    if destination_root.exists() or destination_root.is_symlink():
        raise RuntimeBlocked("runtime-destination-must-be-new")
    destination_root.mkdir(parents=True)
    for entry in manifest["files"]:
        source_path = runtime_root / entry["path"]
        destination_path = destination_root / entry["path"]
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if "symlink" in entry:
            destination_path.symlink_to(entry["symlink"])
        else:
            shutil.copy2(source_path, destination_path)
    write_json(destination_root / "release-manifest.json", manifest)
    verify_manifest(destination_root, target_name)
    return manifest


def build_application_bundle(
    bundle_root: Path, target_name: str, target: dict, arguments,
) -> dict:
    """Assemble a new immutable payload; preserve earlier recognized bundles."""
    if bundle_root.is_symlink() or bundle_root.resolve() != bundle_root.absolute():
        raise RuntimeBlocked("bundle-root-must-not-be-symlinked")
    if bundle_root.exists():
        if not arguments.rebuild:
            raise RuntimeBlocked("bundle-exists-use-rebuild-to-preserve")
        try:
            previous_manifest = json.loads((bundle_root / "release-manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeBlocked("bundle-rebuild-refuses-unrecognized-payload") from error
        if previous_manifest.get("scope") != resources.APPLICATION_SCOPE or previous_manifest.get("target") != target_name:
            raise RuntimeBlocked("bundle-rebuild-refuses-unrecognized-payload")
    # Fail before runtime downloads when the lifecycle work has not been installed.
    for relative_path in resources.FIXED_RESOURCES:
        if not relative_path.startswith("assistant/web/static/"):
            resources.require_regular_resource(ROOT, relative_path)
    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="application-build-", dir=bundle_root.parent) as temporary:
        staging_root = Path(temporary) / "payload"
        runtime_source_root = ROOT / "build/release" / target_name
        if (runtime_source_root / "release-manifest.json").is_file():
            copy_verified_runtime(runtime_source_root, staging_root, target_name)
        else:
            build_runtime(staging_root, target_name, target, arguments)
        runtime_manifest_path = staging_root / "release-manifest.json"
        runtime_manifest = verify_manifest(staging_root, target_name)
        build_static_assets(staging_root, target)
        resources.copy_application_resources(ROOT, staging_root)
        resources.write_platform_launchers(ROOT, staging_root, target_name, target["python_executable"])
        remove_python_bytecode(staging_root)
        runtime_manifest_path.chmod(0o644)
        runtime_manifest_path.unlink()
        manifest = create_manifest(
            staging_root, target_name, target, runtime_manifest["ffmpeg_recipe"],
            scope=resources.APPLICATION_SCOPE,
        )
        write_json(runtime_manifest_path, manifest)
        verify_manifest(staging_root, target_name)
        runtime_manifest_path.chmod(0o444)
        if bundle_root.exists():
            # Keep generated history outside the 006 runtime-proof root.
            history_root = bundle_root.parent.parent / ".previous"
            if history_root.is_symlink():
                raise RuntimeBlocked("build-history-root-must-not-be-symlinked")
            history_root.mkdir(exist_ok=True)
            preserved_root = Path(tempfile.mkdtemp(prefix="bundle-", dir=history_root))
            bundle_root.rename(preserved_root / "payload")
        staging_root.rename(bundle_root)
    return manifest


def build_runtime(bundle_root: Path, target_name: str, target: dict, arguments) -> None:
    if (bundle_root / "release-manifest.json").exists():
        if not arguments.rebuild:
            return
        existing = json.loads(
            (bundle_root / "release-manifest.json").read_text(encoding="utf-8")
        )
        if (
            existing.get("scope") != "runtime-proof-not-full-application"
            or existing.get("target") != target_name
        ):
            raise RuntimeBlocked("rebuild-refuses-unrecognized-payload")
        history_root = bundle_root.parent / ".previous"
        history_root.mkdir(exist_ok=True)
        if history_root.is_symlink():
            raise RuntimeBlocked("build-history-root-must-not-be-symlinked")
        preserved_directory = Path(
            tempfile.mkdtemp(prefix=target_name + "-", dir=history_root)
        )
        bundle_root.rename(preserved_directory / "payload")
        logger.info("Preserved the previous generated payload before rebuilding")
    if (bundle_root / "runtime").exists():
        raise RuntimeBlocked("incomplete-build-needs-manual-inspection")
    uv_path = resolve_build_tool(arguments.uv, "uv")
    cache_root = bundle_root.parent / ".downloads"
    bundle_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="runtime-build-", dir=bundle_root.parent
    ) as temporary:
        staging_root = Path(temporary)
        (staging_root / "runtime").mkdir()
        for runtime_name in ("python", "node"):
            logger.info("Downloading and normalizing private %s", runtime_name)
            unpack_runtime(target[runtime_name], staging_root, cache_root, runtime_name)
        python_path = staging_root / target["python_executable"]
        artifacts.validate_binary_architecture(python_path, target)
        requirements_path = staging_root / "production-requirements.txt"
        export_production_requirements(uv_path, requirements_path)
        logger.info("Installing locked production wheels into the private interpreter")
        run_checked(
            [
                uv_path,
                "pip",
                "install",
                "--python",
                str(python_path),
                "--prefix",
                str(staging_root / "runtime" / "python"),
                "--requirements",
                str(requirements_path),
                "--require-hashes",
                "--no-deps",
                "--only-binary",
                ":all:",
                "--link-mode",
                "copy",
                "--no-config",
                "--no-python-downloads",
            ],
            cwd=staging_root,
            label="production-wheels",
            timeout=300,
        )
        remove_upstream_package_manager(staging_root / "runtime/python", target)
        # Console-script shebangs embed build paths. Only sys.executable -m ...
        # will be used; keep libraries/dist-info, not these nonrelocatable shims.
        if target["system"] == "Darwin":
            for script in (staging_root / "runtime/python/bin").iterdir():
                if script.is_file() and not script.is_symlink():
                    with script.open("rb") as stream:
                        if stream.read(2) == b"#!":
                            script.unlink()
        else:
            shutil.rmtree(staging_root / "runtime/python/Scripts", ignore_errors=True)
        recipe = build_ffmpeg(staging_root, cache_root, target, arguments)
        manifest = create_manifest(staging_root, target_name, target, recipe)
        write_json(staging_root / "release-manifest.json", manifest)
        for resource in staging_root.iterdir():
            resource.rename(bundle_root / resource.name)
        (bundle_root / "release-manifest.json").chmod(0o444)


def remove_upstream_package_manager(python_root: Path, target: dict) -> None:
    """The standalone archive's pip carries an upstream build-directory URL.

    End users do not install dependencies. Removing this optional build tool avoids
    shipping that absolute file reference without altering production dist-info.
    """
    site_packages = python_root / (
        "Lib/site-packages"
        if target["system"] == "Windows"
        else f"lib/python{policy.PINNED_PYTHON}/site-packages"
    )
    pip_package = site_packages / "pip"
    if pip_package.exists():
        shutil.rmtree(pip_package)
    for metadata in site_packages.glob("pip-*.dist-info"):
        shutil.rmtree(metadata)


def verify_manifest(
    bundle_root: Path, target_name: str, *, verify_nested_bundle: bool = True,
) -> dict:
    try:
        manifest = json.loads(
            (bundle_root / "release-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise RuntimeBlocked("runtime-manifest-required") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise RuntimeBlocked("invalid-runtime-manifest")
    target = artifacts.get_target(target_name)
    if manifest.get("target") != target_name or manifest.get("schema_version") != 1:
        raise RuntimeBlocked("runtime-manifest-target-mismatch")
    if manifest.get("uv_lock_sha256") != artifacts.hash_file(ROOT / "uv.lock"):
        raise RuntimeBlocked("runtime-lock-drift-rebuild-required")
    for name, expected in (
        ("python", target["python"]),
        ("node", target["node"]),
        ("ffmpeg", policy.RELEASE_FFMPEG_SOURCE),
    ):
        if manifest.get("sources", {}).get(name) != expected:
            raise RuntimeBlocked("runtime-source-policy-drift")
    expected_paths = set()
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise RuntimeBlocked("invalid-runtime-manifest-entry")
        artifacts.validate_member_name(entry["path"])
        entry_path = Path(entry["path"])
        if manifest.get("scope") == resources.APPLICATION_SCOPE and (
            "__pycache__" in entry_path.parts
            or entry_path.suffix in {".pyc", ".pyo"}
        ):
            raise RuntimeBlocked("application-bytecode-forbidden")
        path = bundle_root / entry["path"]
        if entry["path"] in expected_paths:
            raise RuntimeBlocked("runtime-manifest-duplicate-entry")
        expected_paths.add(entry["path"])
        if not path.resolve().is_relative_to(bundle_root.resolve()):
            raise RuntimeBlocked("runtime-file-escape")
        if "symlink" in entry:
            if not path.is_symlink() or os.readlink(path) != entry["symlink"]:
                raise RuntimeBlocked("runtime-symlink-drift")
        elif (
            path.is_symlink()
            or not path.is_file()
            or artifacts.hash_file(path) != entry["sha256"]
        ):
            raise RuntimeBlocked("runtime-file-hash-mismatch")
    managed_bundle = bundle_root / "bundle"
    has_managed_bundle = (
        manifest.get("scope") == "runtime-proof-not-full-application"
        and (managed_bundle.exists() or managed_bundle.is_symlink())
    )
    if has_managed_bundle and verify_nested_bundle:
        if managed_bundle.is_symlink():
            raise RuntimeBlocked("runtime-nested-bundle-symlink")
        nested_manifest = verify_manifest(managed_bundle, target_name)
        if nested_manifest.get("scope") != resources.APPLICATION_SCOPE:
            raise RuntimeBlocked("runtime-nested-bundle-unrecognized")
    actual_paths = {
        path.relative_to(bundle_root).as_posix()
        for path in bundle_root.rglob("*")
        if (path.is_file() or path.is_symlink())
        and path.name != "release-manifest.json"
        and not (has_managed_bundle and path.is_relative_to(managed_bundle))
    }
    if not expected_paths or actual_paths != expected_paths:
        raise RuntimeBlocked("runtime-unmanifested-or-missing-files")
    if (bundle_root / "runtime/ziniao").exists():
        raise RuntimeBlocked("external-cli-must-not-be-bundled")
    return manifest


def remove_python_bytecode(bundle_root: Path) -> None:
    """Exclude interpreter cache artifacts from every release payload."""
    for bytecode_directory in sorted(bundle_root.rglob("__pycache__"), reverse=True):
        if bytecode_directory.is_symlink():
            bytecode_directory.unlink()
        elif bytecode_directory.is_dir():
            shutil.rmtree(bytecode_directory)
    for suffix in ("*.pyc", "*.pyo"):
        for bytecode_path in bundle_root.rglob(suffix):
            if bytecode_path.is_symlink() or bytecode_path.is_file():
                bytecode_path.unlink()


def verify_ffmpeg_license(license_output: str, version_output: str) -> None:
    normalized_license = " ".join(license_output.split())
    if "GNU Lesser General Public License" not in normalized_license or any(
        flag in version_output
        for flag in ("--enable-gpl", "--enable-nonfree", "--enable-version3")
    ):
        raise RuntimeBlocked("ffmpeg-license-policy-mismatch")


def probe_runtime(
    bundle_root: Path,
    target_name: str,
    target: dict,
    runner_path: Path | None,
    working_directory: Path,
) -> dict:
    verify_manifest(bundle_root, target_name)
    python_path, node_path, ffmpeg_path = [
        bundle_root / target[field]
        for field in ("python_executable", "node_executable", "ffmpeg_executable")
    ]
    for executable in (python_path, node_path, ffmpeg_path):
        artifacts.validate_binary_architecture(executable, target)
    python_result = run_checked(
        [
            str(python_path),
            "-I",
            "-B",
            "-c",
            PYTHON_PROBE,
            policy.RELEASE_PYTHON_VERSION,
        ],
        cwd=working_directory,
        label="python-probe",
    )
    python_data = json.loads(python_result.stdout)
    node_version = run_checked(
        [str(node_path), "--version"], cwd=working_directory, label="node-version"
    ).stdout.strip()
    if node_version != "v" + policy.RELEASE_NODE_VERSION:
        raise RuntimeBlocked("node-version-mismatch")
    ffmpeg_version = run_checked(
        [str(ffmpeg_path), "-version"], cwd=working_directory, label="ffmpeg-version"
    ).stdout
    if not ffmpeg_version.startswith(
        f"ffmpeg version {policy.RELEASE_FFMPEG_VERSION} "
    ):
        raise RuntimeBlocked("ffmpeg-version-mismatch")
    ffmpeg_license = run_checked(
        [str(ffmpeg_path), "-L"], cwd=working_directory, label="ffmpeg-license"
    ).stdout
    verify_ffmpeg_license(ffmpeg_license, ffmpeg_version)
    with tempfile.TemporaryDirectory(
        prefix="media-probe-", dir=working_directory
    ) as media_temporary:
        media_directory = Path(media_temporary)
        raw_path = media_directory / "synthetic.rgb"
        video_path = media_directory / "synthetic.mp4"
        raw_path.write_bytes(bytes((64, 128, 192)) * (16 * 16 * 4))
        run_checked(
            [
                str(ffmpeg_path),
                "-nostdin",
                "-v",
                "error",
                "-f",
                "rawvideo",
                "-pixel_format",
                "rgb24",
                "-video_size",
                "16x16",
                "-framerate",
                "1",
                "-i",
                str(raw_path),
                "-c:v",
                "mpeg4",
                "-y",
                str(video_path),
            ],
            cwd=working_directory,
            label="synthetic-media-encode",
        )
        image_result = run_checked(
            [
                str(ffmpeg_path),
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-f",
                "mov",
                "-i",
                str(video_path),
                "-vf",
                "scale=8:8",
                "-frames:v",
                "1",
                "-c:v",
                "mjpeg",
                "-f",
                "image2pipe",
                "pipe:1",
            ],
            cwd=working_directory,
            label="synthetic-media-decode",
            binary=True,
        )
        if not image_result.stdout.startswith(
            b"\xff\xd8"
        ) or not image_result.stdout.endswith(b"\xff\xd9"):
            raise RuntimeBlocked("synthetic-media-jpeg-not-confirmed")
    if runner_path is None:
        raise RuntimeBlocked("external-cli-explicit-runner-required")
    external = artifacts.resolve_external_cli(runner_path, bundle_root, target)
    artifacts.validate_binary_architecture(external["binary"], target)
    cli_result = run_checked(
        [str(node_path), external["runner"].as_posix(), "--version"],
        cwd=working_directory,
        label="external-cli-version",
        timeout=15,
    )
    if not re.fullmatch(
        r"(?:ziniao-cli(?: version)?\s+)?v?" + re.escape(policy.PINNED_ZINIAO_CLI),
        cli_result.stdout.strip(),
    ):
        raise RuntimeBlocked("external-cli-version-only-output-required")
    if cli_result.stderr.strip():
        raise RuntimeBlocked("external-cli-unexpected-stderr")
    dependency_audit = (
        artifacts.audit_macos_dependencies(bundle_root)
        if target["system"] == "Darwin"
        else artifacts.audit_windows_dependencies(bundle_root)
    )
    return {
        "python": python_data,
        "node": {"version": node_version},
        "ffmpeg": {
            "version": policy.RELEASE_FFMPEG_VERSION,
            "license_verified": True,
            "synthetic_decode_verified": True,
        },
        "external_cli": {
            **policy.RELEASE_EXTERNAL_CLI,
            "bundled": False,
            "version_verified": True,
            "runner_sha256": external["runner_sha256"],
            "binary_sha256": external["binary_sha256"],
            "scope": "version probe only; opaque business paths not exercised",
        },
        "dependency_audit": dependency_audit,
    }


def execute(arguments) -> tuple[dict, int]:
    original_environment = dict(os.environ)
    report = {
        "target": arguments.target,
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "os_version": platform.mac_ver()[0] or platform.version(),
        },
        "native_verified": False,
        "relocation_verified": False,
        "clean_environment_verified": False,
        "checks": [],
    }
    try:
        target = artifacts.get_target(arguments.target)
        artifacts.require_native_host(target)
        report["checks"].append({"name": "native_host", "status": "passed"})
        bundle_root = ROOT / "build/release" / arguments.target
        if bundle_root.resolve() != bundle_root.absolute():
            raise RuntimeBlocked("build-root-must-not-be-symlinked")
        if arguments.command == "bundle":
            manifest = build_application_bundle(bundle_root / "bundle", arguments.target, target, arguments)
            report["checks"].append({"name": "full_application_assembly", "status": "passed"})
            report.update(
                status="PASSED", scope=manifest["scope"],
                application_version=manifest["application_version"],
                bundle=f"build/release/{arguments.target}/bundle",
                bundle_smoke_verified=False,
            )
            return report, 0
        if arguments.command == "probe":
            build_runtime(bundle_root, arguments.target, target, arguments)
        report["checks"].append(
            {
                "name": "manifest",
                "status": "passed",
                "target": verify_manifest(bundle_root, arguments.target)["target"],
            }
        )
        runner_path = Path(arguments.ziniao_runner) if arguments.ziniao_runner else None
        with tempfile.TemporaryDirectory(prefix="runtime-probe-") as temporary:
            report["probes"] = probe_runtime(
                bundle_root, arguments.target, target, runner_path, Path(temporary)
            )
        report["native_verified"] = True
        report["checks"].append({"name": "native_runtime_probes", "status": "passed"})
        if arguments.command == "verify-runtime":
            with tempfile.TemporaryDirectory(prefix="runtime-relocation-") as temporary:
                relocated_root = (
                    Path(temporary) / "\u79c1\u6709\u8fd0\u884c\u65f6 with spaces"
                )
                shutil.copytree(bundle_root, relocated_root, symlinks=True)
                working_directory = Path(temporary) / "unrelated-working-directory"
                working_directory.mkdir()
                report["relocated_probes"] = probe_runtime(
                    relocated_root,
                    arguments.target,
                    target,
                    runner_path,
                    working_directory,
                )
            report["relocation_verified"] = True
            report["checks"].append({"name": "relocation", "status": "passed"})
            # No flag can turn this development-machine result into clean-machine
            # acceptance. Arrange a new user/VM/device and record native evidence.
            report["checks"].append(
                {
                    "name": "clean_environment",
                    "status": "blocked",
                    "reason": "independent-clean-machine-acceptance-required",
                }
            )
            report["status"] = "BLOCKED"
            return report, 2
        report["status"] = "PASSED"
        return report, 0
    except (RuntimeBlocked, OSError, ValueError, KeyError) as error:
        report["status"] = "BLOCKED"
        report["checks"].append(
            {
                "name": "runtime_verification",
                "status": "blocked",
                "reason": str(error)
                if isinstance(error, RuntimeBlocked)
                else type(error).__name__,
            }
        )
        return report, 2
    finally:
        report["parent_environment_unchanged"] = (
            dict(os.environ) == original_environment
        )


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("probe", "verify-runtime", "bundle"))
    parser.add_argument(
        "--target", required=True, choices=tuple(policy.RELEASE_TARGETS)
    )
    parser.add_argument(
        "--ziniao-runner",
        help="Absolute path to technician-installed scripts/run.js; never copied",
    )
    parser.add_argument("--uv", help="Build-only absolute uv executable")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Probe/bundle: preserve an existing generated payload and build afresh",
    )
    parser.add_argument(
        "--ffmpeg-shell", help="Build-only absolute native shell executable"
    )
    parser.add_argument(
        "--ffmpeg-cc", help="Build-only absolute native C compiler executable"
    )
    parser.add_argument(
        "--ffmpeg-make", help="Build-only absolute native make executable"
    )
    arguments = parser.parse_args()
    if arguments.rebuild and arguments.command not in {"probe", "bundle"}:
        parser.error("--rebuild is only supported by probe or bundle")
    report, exit_code = execute(arguments)
    print(json.dumps(report, ensure_ascii=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
