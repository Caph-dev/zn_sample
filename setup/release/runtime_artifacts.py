"""Release policy readers, verified downloads and defensive archive extraction.

This module never discovers global runtime tools or reads application configuration.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import struct
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

POLICY_PATH = Path(__file__).resolve().parents[1] / "install_deps.py"
policy_spec = importlib.util.spec_from_file_location(
    "release_dependency_policy", POLICY_PATH
)
assert policy_spec is not None and policy_spec.loader is not None
policy = importlib.util.module_from_spec(policy_spec)
policy_spec.loader.exec_module(policy)


class RuntimeBlocked(RuntimeError):
    """A prerequisite could not be proved. Messages contain no configuration values."""


def get_target(target_name: str) -> dict:
    if target_name not in policy.RELEASE_TARGETS:
        raise RuntimeBlocked("unsupported-target")
    target = policy.RELEASE_TARGETS[target_name]
    if not policy.RELEASE_PYTHON_VERSION.startswith(policy.PINNED_PYTHON + "."):
        raise RuntimeBlocked("python-policy-mismatch")
    node_version = policy.parse_version(policy.RELEASE_NODE_VERSION)
    if node_version is None or not policy.node_is_supported(node_version):
        raise RuntimeBlocked("node-engine-policy-mismatch")
    for artifact in (target["python"], target["node"], policy.RELEASE_FFMPEG_SOURCE):
        validate_artifact(artifact)
    return target


def require_native_host(target: dict) -> None:
    if (
        platform.system() != target["system"]
        or platform.machine().lower() != target["machine"].lower()
    ):
        raise RuntimeBlocked(
            "native-host-required; cross-execution-is-not-verification"
        )
    if platform.system() == "Darwin":
        host_version = tuple(int(part) for part in platform.mac_ver()[0].split("."))
        minimum_version = tuple(int(part) for part in target["minimum_os"].split("."))
        if host_version < minimum_version:
            raise RuntimeBlocked("host-below-node-runtime-minimum")


def validate_artifact(artifact: dict) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", artifact.get("sha256", "")):
        raise RuntimeBlocked("trusted-sha256-required")
    for field in ("url", "checksum_source", "license_source"):
        if not artifact.get(field, "").startswith("https://"):
            raise RuntimeBlocked("https-source-and-license-required")
    if not artifact.get("version"):
        raise RuntimeBlocked("exact-artifact-version-required")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(artifact: dict, destination: Path, *, opener=None) -> Path:
    """Only publish a download after comparing a pre-existing trusted checksum."""
    validate_artifact(artifact)
    if destination.exists():
        if hash_file(destination) == artifact["sha256"]:
            return destination
        raise RuntimeBlocked("cached-artifact-checksum-mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_suffix(destination.suffix + ".partial")
    try:
        with (opener or urllib.request.urlopen)(
            artifact["url"], timeout=120
        ) as response:
            if hasattr(response, "geturl") and not response.geturl().startswith(
                "https://"
            ):
                raise RuntimeBlocked("insecure-download-redirect")
            with temporary_path.open("wb") as output:
                shutil.copyfileobj(response, output)
        if hash_file(temporary_path) != artifact["sha256"]:
            raise RuntimeBlocked("download-checksum-mismatch")
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def validate_member_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or path.is_absolute()
        or PureWindowsPath(name).drive
        or ".." in path.parts
        or ":" in name
    ):
        raise RuntimeBlocked("archive-path-escape")
    return path


def extract_verified_archive(archive_path: Path, destination: Path) -> None:
    """Reject traversal, special files, duplicate paths and escaping link chains.

    Destinations must be empty. Files are extracted before symlinks, preventing an
    archive entry from writing through a link created by another entry.
    """
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeBlocked("archive-destination-not-empty")
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    seen_names: set[str] = set()

    def register_member(name: str) -> Path:
        relative = validate_member_name(name)
        normalized = relative.as_posix().casefold()
        if normalized in seen_names:
            raise RuntimeBlocked("archive-duplicate-path")
        seen_names.add(normalized)
        output_path = destination / relative
        if not output_path.resolve().is_relative_to(destination):
            raise RuntimeBlocked("archive-path-escape")
        return output_path

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            entries = [
                (member, register_member(member.filename))
                for member in archive.infolist()
            ]
            for member, _ in entries:
                file_type = stat.S_IFMT(member.external_attr >> 16)
                if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise RuntimeBlocked("zip-special-file-or-symlink")
            for member, output_path in entries:
                if member.is_dir():
                    output_path.mkdir(parents=True, exist_ok=True)
                else:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        archive.open(member) as source,
                        output_path.open("xb") as output,
                    ):
                        shutil.copyfileobj(source, output)
                    output_path.chmod((member.external_attr >> 16) & 0o777 or 0o644)
        return

    with tarfile.open(archive_path, "r:*") as archive:
        entries = [
            (member, register_member(member.name)) for member in archive.getmembers()
        ]
        symlink_paths = {
            output_path for member, output_path in entries if member.issym()
        }
        for member, output_path in entries:
            if any(parent in symlink_paths for parent in output_path.parents):
                raise RuntimeBlocked("archive-entry-through-symlink")
            if not (member.isfile() or member.isdir() or member.issym()):
                raise RuntimeBlocked("tar-special-file-or-hardlink")
            if member.issym():
                # Relative parent references are valid only if they stay inside the root.
                if "\\" in member.linkname or PureWindowsPath(member.linkname).drive:
                    raise RuntimeBlocked("archive-symlink-escape")
                if PurePosixPath(member.linkname).is_absolute():
                    raise RuntimeBlocked("archive-symlink-escape")
                link_target = (output_path.parent / member.linkname).resolve()
                if not link_target.is_relative_to(destination):
                    raise RuntimeBlocked("archive-symlink-escape")
        for member, output_path in entries:
            if member.isdir():
                output_path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeBlocked("archive-missing-file-data")
                with source, output_path.open("xb") as output:
                    shutil.copyfileobj(source, output)
                output_path.chmod(member.mode & 0o777)
        for member, output_path in entries:
            if member.issym():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.symlink_to(member.linkname)
        for _, output_path in entries:
            try:
                if not output_path.resolve().is_relative_to(destination):
                    raise RuntimeBlocked("archive-symlink-chain-escape")
            except RuntimeError as error:
                raise RuntimeBlocked("archive-symlink-cycle") from error


def isolated_environment(parent_environment=None) -> dict[str, str]:
    environment = dict(os.environ if parent_environment is None else parent_environment)
    for variable in tuple(environment):
        if variable.startswith(("PYTHON", "NODE_", "DYLD_", "LD_")) or variable in {
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "__PYVENV_LAUNCHER__",
        }:
            environment.pop(variable)
    environment.update(PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return environment


def resolve_external_cli(runner_path: Path, bundle_root: Path, target: dict) -> dict:
    """Only an explicit preinstalled package is allowed; never install or copy it."""
    if not runner_path.is_absolute():
        raise RuntimeBlocked("external-cli-absolute-runner-required")
    runner_path = runner_path.resolve()
    if runner_path.is_relative_to(bundle_root.resolve()):
        raise RuntimeBlocked("external-cli-must-not-be-bundled")
    package_root = runner_path.parent.parent
    if runner_path.name != "run.js" or runner_path.parent.name != "scripts":
        raise RuntimeBlocked("external-cli-runner-layout-mismatch")
    try:
        metadata = json.loads(
            (package_root / "package.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise RuntimeBlocked("external-cli-package-missing") from error
    if not isinstance(metadata, dict) or any(
        metadata.get(field) != expected
        for field, expected in policy.RELEASE_EXTERNAL_CLI.items()
    ):
        raise RuntimeBlocked("external-cli-fixed-version-required")
    binary_path = (
        package_root
        / "bin"
        / ("ziniao-cli.exe" if target["system"] == "Windows" else "ziniao-cli")
    )
    if not runner_path.is_file() or not binary_path.is_file():
        raise RuntimeBlocked("external-cli-technician-install-incomplete")
    wrapper = runner_path.read_text(encoding="utf-8")
    if "spawnSync(binaryPath, process.argv.slice(2)" not in wrapper:
        raise RuntimeBlocked("external-cli-wrapper-requires-review")
    # A version probe cannot prove all opaque native-binary business paths.
    if re.search(r"(?:exec|spawn)(?:File|Sync)?\s*\(\s*['\"](?:node|npm|npx)", wrapper):
        raise RuntimeBlocked("external-cli-global-node-chain")
    runner_digest = hash_file(runner_path)
    if runner_digest != policy.RELEASE_EXTERNAL_CLI_RUNNER_SHA256:
        raise RuntimeBlocked("external-cli-wrapper-drift-requires-review")
    if binary_path.resolve().is_relative_to(bundle_root.resolve()):
        raise RuntimeBlocked("external-cli-must-not-be-bundled")
    return {
        "runner": runner_path,
        "binary": binary_path,
        "runner_sha256": runner_digest,
        "binary_sha256": hash_file(binary_path),
    }


def inspect_macho(path: Path) -> dict | None:
    with path.open("rb") as stream:
        header = stream.read(32)
        maximum_command_bytes = max(path.stat().st_size - 32, 0)
        fat_formats = {
            b"\xca\xfe\xba\xbe": (">", False),
            b"\xbe\xba\xfe\xca": ("<", False),
            b"\xca\xfe\xba\xbf": (">", True),
            b"\xbf\xba\xfe\xca": ("<", True),
        }
        if header[:4] in fat_formats:
            endianness, is_fat64 = fat_formats[header[:4]]
            if len(header) < 8:
                raise RuntimeBlocked("invalid-universal-mach-o-header")
            architecture_count = struct.unpack_from(endianness + "I", header, 4)[0]
            if not 1 <= architecture_count <= 64:
                raise RuntimeBlocked("invalid-universal-mach-o-architecture-count")
            record_format = endianness + ("IIQQII" if is_fat64 else "IIIII")
            record_size = struct.calcsize(record_format)
            stream.seek(8)
            records = stream.read(architecture_count * record_size)
            if len(records) != architecture_count * record_size:
                raise RuntimeBlocked("invalid-universal-mach-o-architecture-table")
            slices = [
                struct.unpack_from(record_format, records, index * record_size)
                for index in range(architecture_count)
            ]
            arm64_slices = [record for record in slices if record[0] == 0x0100000C]
            if len(arm64_slices) != 1:
                raise RuntimeBlocked("universal-mach-o-requires-one-arm64-slice")
            _, _, slice_offset, slice_size, *_ = arm64_slices[0]
            if (
                slice_offset < 8 + len(records)
                or slice_size < 32
                or slice_offset + slice_size > path.stat().st_size
            ):
                raise RuntimeBlocked("invalid-universal-mach-o-slice-bounds")
            stream.seek(slice_offset)
            header = stream.read(32)
            maximum_command_bytes = slice_size - 32
        if header[:4] != b"\xcf\xfa\xed\xfe":
            return None
        if len(header) != 32:
            raise RuntimeBlocked("invalid-mach-o-header")
        _, cpu_type, _, file_type, command_count, command_bytes, _, _ = struct.unpack(
            "<8I", header
        )
        if command_bytes > min(16 * 1024 * 1024, maximum_command_bytes):
            raise RuntimeBlocked("invalid-mach-o-command-size")
        commands = stream.read(command_bytes)
        if len(commands) != command_bytes or command_count > command_bytes // 8:
            raise RuntimeBlocked("invalid-mach-o-command-count")
    libraries, rpaths, minimum_versions = [], [], []
    offset = 0
    for _ in range(command_count):
        if offset + 8 > len(commands):
            raise RuntimeBlocked("invalid-mach-o-command")
        command, size = struct.unpack_from("<II", commands, offset)
        if size < 8 or offset + size > len(commands):
            raise RuntimeBlocked("invalid-mach-o-command")
        if command in (
            0xC,
            0x18 | 0x80000000,
            0x1F | 0x80000000,
            0x23 | 0x80000000,
            0x1C | 0x80000000,
        ):
            if size < 12:
                raise RuntimeBlocked("invalid-mach-o-path-command")
            text_offset = struct.unpack_from("<I", commands, offset + 8)[0]
            if not 12 <= text_offset < size:
                raise RuntimeBlocked("invalid-mach-o-path-offset")
            value = (
                commands[offset + text_offset : offset + size]
                .split(b"\0", 1)[0]
                .decode()
            )
            (rpaths if command == 0x1C | 0x80000000 else libraries).append(value)
        if command in (0x24, 0x32):
            if size < 16:
                raise RuntimeBlocked("invalid-mach-o-version-command")
            packed_version = struct.unpack_from(
                "<I", commands, offset + (12 if command == 0x32 else 8)
            )[0]
            minimum_versions.append(
                f"{packed_version >> 16}.{(packed_version >> 8) & 255}.{packed_version & 255}"
            )
        offset += size
    return {
        "cpu_type": cpu_type,
        "file_type": file_type,
        "libraries": libraries,
        "rpaths": rpaths,
        "minimum_versions": minimum_versions,
    }


def audit_macos_dependencies(bundle_root: Path) -> dict:
    """Inspect every thin Mach-O, including extension modules, not only executables."""
    bundle_root = bundle_root.resolve()
    binary_count, dependency_count = 0, 0
    minimum_versions: set[str] = set()
    executable_roots = [
        bundle_root / Path(policy.RELEASE_TARGETS["macos-arm64"][field]).parent
        for field in ("python_executable", "node_executable", "ffmpeg_executable")
    ]
    for binary_path in (bundle_root / "runtime").rglob("*"):
        if binary_path.is_symlink():
            if not binary_path.resolve().is_relative_to(bundle_root):
                raise RuntimeBlocked("runtime-symlink-escape")
            continue
        if not binary_path.is_file():
            continue
        metadata = inspect_macho(binary_path)
        if metadata is None:
            if binary_path.suffix.lower() in (".so", ".dylib", ".node"):
                relative_path = binary_path.relative_to(bundle_root).as_posix()
                raise RuntimeBlocked(
                    f"unsupported-native-library-format:{relative_path}"
                )
            continue
        if metadata["cpu_type"] != 0x0100000C:
            raise RuntimeBlocked("non-arm64-binary-in-runtime")
        binary_count += 1
        minimum_versions.update(metadata["minimum_versions"])

        def expand_path(value: str, executable_root: Path) -> Path:
            return Path(
                value.replace("@loader_path", str(binary_path.parent)).replace(
                    "@executable_path", str(executable_root)
                )
            ).resolve()

        for library in metadata["libraries"]:
            dependency_count += 1
            if library.startswith(("/usr/lib/", "/System/Library/")):
                continue
            candidates: list[Path] = []
            for executable_root in executable_roots:
                if library.startswith("@rpath/"):
                    candidates.extend(
                        expand_path(rpath, executable_root) / library[len("@rpath/") :]
                        for rpath in metadata["rpaths"]
                    )
                    # CPython extensions may inherit libpython's executable rpath.
                    candidates.append(
                        executable_root.parent / "lib" / library[len("@rpath/") :]
                    )
                else:
                    candidates.append(expand_path(library, executable_root))
            if not any(
                candidate.is_file() and candidate.resolve().is_relative_to(bundle_root)
                for candidate in candidates
            ):
                raise RuntimeBlocked("non-bundled-mach-o-dependency")
    if binary_count < 3:
        raise RuntimeBlocked("incomplete-mach-o-runtime")
    return {
        "binary_count": binary_count,
        "dependency_count": dependency_count,
        "minimum_os_versions": sorted(minimum_versions),
        "scope": "bundle-or-apple-system-libraries",
    }


def validate_binary_architecture(path: Path, target: dict) -> None:
    if target["system"] == "Darwin":
        metadata = inspect_macho(path)
        if metadata is None or metadata["cpu_type"] != 0x0100000C:
            raise RuntimeBlocked("expected-native-arm64-mach-o")
    else:
        with path.open("rb") as stream:
            header = stream.read(64)
            if header[:2] != b"MZ" or len(header) < 64:
                raise RuntimeBlocked("expected-native-x64-pe")
            stream.seek(struct.unpack_from("<I", header, 60)[0])
            signature = stream.read(6)
        if signature != b"PE\0\0\x64\x86":
            raise RuntimeBlocked("expected-native-x64-pe")


def inspect_pe_imports(path: Path) -> list[str]:
    """Read regular and delay-load imports in native PE32+ images."""
    data = path.read_bytes()
    try:
        pe_offset = struct.unpack_from("<I", data, 60)[0]
        section_count = struct.unpack_from("<H", data, pe_offset + 6)[0]
        optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
        optional_offset = pe_offset + 24
        if struct.unpack_from("<H", data, optional_offset)[0] != 0x20B:
            raise RuntimeBlocked("expected-pe32-plus")
        section_offset = optional_offset + optional_size
        sections = [
            struct.unpack_from("<IIII", data, section_offset + index * 40 + 8)
            for index in range(section_count)
        ]

        def resolve_address(address: int) -> int:
            for virtual_size, virtual_address, raw_size, raw_offset in sections:
                if (
                    virtual_address
                    <= address
                    < virtual_address + max(virtual_size, raw_size)
                ):
                    return raw_offset + address - virtual_address
            raise RuntimeBlocked("pe-import-address-out-of-bounds")

        imports: list[str] = []
        for directory_index, descriptor_size, name_offset in ((1, 20, 12), (13, 32, 4)):
            address, size = struct.unpack_from(
                "<II", data, optional_offset + 112 + directory_index * 8
            )
            if not address:
                continue
            directory_offset = resolve_address(address)
            for descriptor_offset in range(
                directory_offset, directory_offset + size, descriptor_size
            ):
                descriptor = data[
                    descriptor_offset : descriptor_offset + descriptor_size
                ]
                if not any(descriptor):
                    break
                if (
                    directory_index == 13
                    and struct.unpack_from("<I", descriptor)[0] != 1
                ):
                    raise RuntimeBlocked(
                        "pe-delay-import-absolute-address-not-supported"
                    )
                name_address = struct.unpack_from("<I", descriptor, name_offset)[0]
                string_offset = resolve_address(name_address)
                name = (
                    data[string_offset : string_offset + 512]
                    .split(b"\0", 1)[0]
                    .decode("ascii")
                )
                if not name or "/" in name or "\\" in name:
                    raise RuntimeBlocked("invalid-pe-import-name")
                imports.append(name.lower())
        return imports
    except (struct.error, UnicodeError) as error:
        raise RuntimeBlocked("invalid-pe-import-table") from error


def audit_windows_dependencies(bundle_root: Path) -> dict:
    """Do not silently resolve runtime DLLs through the developer's PATH."""
    bundle_root = bundle_root.resolve()
    target = get_target("windows-x64")
    system_root = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    binary_paths = [
        path
        for path in (bundle_root / "runtime").rglob("*")
        if path.suffix.lower() in (".exe", ".dll", ".pyd") and path.is_file()
    ]
    binary_count, dependency_count = 0, 0
    for binary_path in binary_paths:
        validate_binary_architecture(binary_path, target)
        binary_count += 1
        for library in inspect_pe_imports(binary_path):
            dependency_count += 1
            if library.startswith(("api-ms-win-", "ext-ms-win-")):
                continue
            runtime_candidates = [
                binary_path.parent / library,
                bundle_root / "runtime/python" / library,
                bundle_root / "runtime/python/DLLs" / library,
            ]
            if any(
                candidate.is_file() and candidate.resolve().is_relative_to(bundle_root)
                for candidate in runtime_candidates
            ):
                continue
            # Compiler runtimes must ship in the bundle even when installed globally.
            is_compiler_runtime = library.startswith(
                ("vcruntime", "msvcp", "libgcc", "libstdc++", "libwinpthread")
            )
            if not is_compiler_runtime and (system_root / library).is_file():
                continue
            raise RuntimeBlocked("non-bundled-pe-dependency")
    if binary_count < 3:
        raise RuntimeBlocked("incomplete-pe-runtime")
    return {
        "binary_count": binary_count,
        "dependency_count": dependency_count,
        "scope": "bundle-or-windows-system32-api-contracts",
    }
