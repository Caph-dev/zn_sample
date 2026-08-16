#!/usr/bin/env python3
"""CLI 入口的 logging / 控制台编码。库代码不要调用 configure_*。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TextIO


def configure_stdio() -> None:
    """Windows 默认 GBK；入口统一成 UTF-8，避免中文和 CLI 输出乱码。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def configure_logging(
    *,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    log_path: Path | None = None,
    console_stream: TextIO | None = None,
    verbose: bool = False,
) -> Path | None:
    """终端只打 %(message)s（业务员框不被时间戳撑歪）。明细可写 log_path。"""
    configure_stdio()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    level = logging.DEBUG if verbose else console_level
    console = logging.StreamHandler(console_stream or sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)

    if log_path is None:
        return None

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)
    return log_path
