#!/usr/bin/env python3
"""定位并调用 ziniao-cli；Windows 绕过 .cmd，子进程固定 UTF-8。

对齐 zn_daren/scripts/lib/zclaw_cli.py。本仓 zclaw 仍自己解析 stdout/stderr JSON。
"""
from __future__ import annotations

import shutil
import subprocess
import importlib.util
import json
import sys
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path

from assistant.paths import (
    ReleasePathError,
    application_resource_dir,
    bundled_tool_path,
    configuration_dir,
    is_packaged_distribution,
    python_subprocess_environment,
)

WhichFn = Callable[[str], str | None]
RunnerFn = Callable[..., subprocess.CompletedProcess]


CLI_NOT_FOUND = (
    "找不到 ziniao-cli。请先安装 @ziniao-open/cli，"
    "并确认当前终端能执行 ziniao-cli --version。"
)
WINDOWS_SHIM_INCOMPLETE = (
    "找到的是 Windows 的 ziniao-cli.cmd/.bat，但当前环境缺少 node "
    "或 @ziniao-open/cli/scripts/run.js。请在能执行 node 的终端重试。"
)


def fs_path(path: str | Path) -> str:
    """绝对路径，统一成 POSIX（Windows 为 C:/Users/...）。

    给 node / Python 子进程用。不改系统 PATH，也不拼接反斜杠。
    """
    return Path(path).expanduser().resolve().as_posix()


def resolve_ziniao_cli_command(*, which: WhichFn | None = None) -> list[str]:
    """返回可直接传给 subprocess 的命令前缀（绝对 POSIX 路径）。"""
    if is_packaged_distribution():
        return [fs_path(bundled_tool_path("node")), fs_path(_resolve_release_cli_runner())]
    which_fn = which or shutil.which
    found = which_fn("ziniao-cli") or which_fn("ziniao-cli.cmd")
    if not found:
        raise RuntimeError(CLI_NOT_FOUND)

    cli_path = Path(found).resolve()
    if cli_path.suffix.lower() in {".cmd", ".bat"}:
        runner = (
            cli_path.parent / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"
        ).resolve()
        node_found = which_fn("node") or which_fn("node.exe")
        if not node_found or not runner.is_file():
            raise RuntimeError(WINDOWS_SHIM_INCOMPLETE)
        return [fs_path(node_found), fs_path(runner)]
    return [fs_path(cli_path)]


@lru_cache(maxsize=1)
def _release_artifacts_module():
    # Reuse 006's audited policy and external CLI checks, not a second version policy.
    module_path = application_resource_dir() / "setup" / "release" / "runtime_artifacts.py"
    specification = importlib.util.spec_from_file_location("release_runtime_artifacts", module_path)
    if specification is None or specification.loader is None or not module_path.is_file():
        raise ReleasePathError("release-policy-missing", "发布包缺少运行时校验模块。")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _resolve_release_cli_runner() -> Path:
    registry_path = configuration_dir() / "external-cli.json"
    try:
        registration = json.loads(registry_path.read_text(encoding="utf-8"))
        if (
            not isinstance(registration, dict)
            or set(registration) != {"schema_version", "runner_path", "binary_sha256"}
            or registration["schema_version"] != 1
            or not isinstance(registration["runner_path"], str)
            or not isinstance(registration["binary_sha256"], str)
        ):
            raise ValueError("invalid registration")
    except (OSError, ValueError) as error:
        raise ReleasePathError("release-cli-registration", "请技术人员登记已预装的固定版本 CLI。") from error
    artifacts = _release_artifacts_module()
    target_name = "windows-x64" if sys.platform == "win32" else "macos-arm64"
    try:
        target = artifacts.get_target(target_name)
        resolved_cli = artifacts.resolve_external_cli(
            Path(registration["runner_path"]), application_resource_dir().parent, target,
        )
        artifacts.validate_binary_architecture(resolved_cli["binary"], target)
    except (artifacts.RuntimeBlocked, OSError, ValueError) as error:
        raise ReleasePathError("release-cli-invalid", "外部 CLI 版本、入口或架构校验失败。") from error
    if resolved_cli["binary_sha256"] != registration["binary_sha256"]:
        raise ReleasePathError("release-cli-hash", "外部 CLI 二进制已变更，请技术人员重新核验。")
    return resolved_cli["runner"]


def run_ziniao_cli(
    args: Sequence[str],
    *,
    timeout: int = 120,
    resolve_command: Callable[[], list[str]] | None = None,
    runner: RunnerFn | None = None,
) -> subprocess.CompletedProcess:
    """执行 ziniao-cli 子命令。固定 UTF-8，不开 shell。"""
    command = (resolve_command or resolve_ziniao_cli_command)()
    run = runner or subprocess.run
    environment_options = {"env": python_subprocess_environment()} if is_packaged_distribution() else {}
    return run(
        [*command, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        **environment_options,
    )
