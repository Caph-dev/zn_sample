#!/usr/bin/env python3
"""Archive an existing Windows bundle, without rebuilding or running business.

CI invokes this only after native runtime checks and offline smoke succeed.
Archive validation is not a substitute for those checks or Windows 10/11 QA.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import tempfile
import zipfile
from pathlib import Path

if __package__:
    from .build_bundle import verify_manifest
    from .resources import APPLICATION_SCOPE
    from .runtime_artifacts import RuntimeBlocked, hash_file
else:
    from build_bundle import verify_manifest
    from resources import APPLICATION_SCOPE
    from runtime_artifacts import RuntimeBlocked, hash_file


logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def verify_archive(archive_path: Path, bundle_root: Path, manifest: dict) -> None:
    expected_hashes = {
        "bundle/" + entry["path"]: entry["sha256"] for entry in manifest["files"]
    }
    expected_hashes["bundle/release-manifest.json"] = hash_file(
        bundle_root / "release-manifest.json"
    )
    with zipfile.ZipFile(archive_path) as archive:
        archive_names = archive.namelist()
        has_duplicate_entries = len(archive_names) != len(set(archive_names))
        has_payload_mismatch = set(archive_names) != set(expected_hashes)
        if has_duplicate_entries or has_payload_mismatch:
            raise RuntimeBlocked("zip-payload-mismatch")
        if archive.testzip() is not None:
            raise RuntimeBlocked("zip-integrity-failed")
        for member_name, expected_hash in expected_hashes.items():
            with archive.open(member_name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected_hash:
                    raise RuntimeBlocked("zip-file-hash-mismatch")


def package_windows_bundle(
    bundle_root: Path, output_directory: Path, source_revision: str,
) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise RuntimeBlocked("source-revision-invalid")
    bundle_root = bundle_root.resolve()
    output_directory = output_directory.resolve()
    if output_directory.is_relative_to(bundle_root):
        raise RuntimeBlocked("archive-output-inside-bundle")
    manifest = verify_manifest(bundle_root, "windows-x64")
    if manifest.get("scope") != APPLICATION_SCOPE:
        raise RuntimeBlocked("complete-windows-bundle-required")
    application_version = manifest.get("application_version")
    if not isinstance(application_version, str) or not re.fullmatch(
        r"[0-9A-Za-z][0-9A-Za-z._+-]*", application_version,
    ):
        raise RuntimeBlocked("application-version-invalid")
    # Windows payloads must contain regular files; never dereference a link.
    if any("symlink" in entry for entry in manifest["files"]):
        raise RuntimeBlocked("windows-archive-symlink-forbidden")

    archive_name = f"zn-sample-{application_version}-windows-x64.zip"
    archive_path = output_directory / archive_name
    checksum_path = output_directory / (archive_name + ".sha256")
    report_path = output_directory / "release-info.json"
    output_paths = (archive_path, checksum_path, report_path)
    if any(path.exists() or path.is_symlink() for path in output_paths):
        raise RuntimeBlocked("archive-output-exists")
    output_directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="windows-archive-", dir=output_directory,
    ) as temporary:
        temporary_archive = Path(temporary) / archive_name
        with zipfile.ZipFile(
            temporary_archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
        ) as archive:
            relative_paths = [entry["path"] for entry in manifest["files"]]
            for relative_path in sorted([*relative_paths, "release-manifest.json"]):
                archive.write(bundle_root / relative_path, "bundle/" + relative_path)
        verify_archive(temporary_archive, bundle_root, manifest)
        report = {
            "schema_version": 1,
            "archive": archive_name,
            "archive_sha256": hash_file(temporary_archive),
            "archive_size_bytes": temporary_archive.stat().st_size,
            "application_version": application_version,
            "target": "windows-x64",
            "source_revision": source_revision,
            "distribution": "internal-test",
            "archive_verified": True,
            "windows_desktop_acceptance": "required",
        }
        # Exclusive creation prevents a rerun from silently replacing a deliverable.
        with archive_path.open("xb") as destination, temporary_archive.open("rb") as source:
            while archive_chunk := source.read(1024 * 1024):
                destination.write(archive_chunk)

    with checksum_path.open("x", encoding="utf-8") as stream:
        stream.write(f"{report['archive_sha256']}  {archive_name}\n")
    with report_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    return report


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    arguments = parser.parse_args()
    try:
        report = package_windows_bundle(
            arguments.bundle, arguments.output_directory, arguments.source_revision,
        )
    except (RuntimeBlocked, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        diagnostic = str(error) if isinstance(error, RuntimeBlocked) else type(error).__name__
        logger.error("Windows archive blocked: %s", diagnostic)
        return 2
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
