#!/usr/bin/env python3
"""定位并调用 ziniao-cli；Windows 绕过 .cmd，子进程固定 UTF-8。

对齐 zn_daren/scripts/lib/zclaw_cli.py。本仓 zclaw 仍自己解析 stdout/stderr JSON。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

WhichFn = Callable[[str], str | None]
RunnerFn = Callable[..., subprocess.CompletedProcess]


def default_which(name: str) -> str | None:
    """更可靠的 which 实现：

    - 优先使用 shutil.which
    - 在 Windows 上若 shutil.which 未找到，回退使用 `where` 命令（可能定位到 .cmd/.ps1 shim）
    - 返回第一个非空路径或 None
    """
    # 首先尝试 shutil.which，这会处理 PATHEXT 与常见场景
    try:
        p = shutil.which(name)
    except Exception:
        p = None
    if p:
        return p

    # 在 Windows 上，shutil.which 有时找不到由 npm 放在 PATH 中的 shim，使用 `where` 作为回退
    if os.name == "nt":
        try:
            proc = subprocess.run(["where", name], capture_output=True, text=True, shell=False)
            if proc.returncode == 0 and proc.stdout:
                # where 可能返回多行，取第一行非空结果
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if line:
                        return line
        except Exception:
            # 忽略并返回 None
            return None

    return None


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
    """返回可直接传给 subprocess 的命令前缀（绝对 POSIX 路径）。

    在 Windows 上要兼容多种 shim：
    - 优先查找常见候选名（ziniao-cli, ziniao-cli.cmd, .bat, .exe, .ps1）
    - 如果找到的是 .cmd/.bat，尝试向上查找可能的 node_modules 中的 run.js（全局或本地安装场景）并用 node 直接运行它
    - 如果找不到 run.js，则回退为直接使用找到的可执行文件（便于在能直接运行 .cmd/.ps1 的环境中工作）
    """
    which_fn = which or default_which

    candidates = [
        "ziniao-cli",
        "ziniao-cli.exe",
        "ziniao-cli.cmd",
        "ziniao-cli.bat",
        "ziniao-cli.ps1",
    ]
    found = None
    for name in candidates:
        p = which_fn(name)
        if p:
            found = p
            break

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
