#!/usr/bin/env python3
"""业务员入口。由仓库根目录的 .command / .bat 调用，不要让人自己敲参数。"""
from __future__ import annotations

import logging

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.app_log import configure_logging  # noqa: E402
from lib.operator_launch import clickable_hint, run_operator_mode  # noqa: E402

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = sys.argv[1:] if argv is None else argv
    if not args:
        logger.info(clickable_hint())
        return 2
    return run_operator_mode(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
