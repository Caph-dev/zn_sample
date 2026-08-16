#!/usr/bin/env python3
"""定位并调用 ziniao-cli；Windows 绕过 .cmd，子进程固定 UTF-8。

对齐 zn_daren/scripts/lib/zclaw_cli.py。本仓 zclaw 仍自己解析 stdout/stderr JSON。
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

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


def resolve_ziniao_cli_command(*, which: WhichFn | None = None) -> list[str]:
    """返回可直接传给 subprocess 的命令前缀（绝对路径）。"""
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
        return [str(Path(node_found).resolve()), str(runner)]
    return [str(cli_path)]


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
    return run(
        [*command, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
    )
