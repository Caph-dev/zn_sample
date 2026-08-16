#!/usr/bin/env python3
"""业务员入口。由仓库根目录的 .command / .bat 调用，不要让人自己敲参数。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.operator_launch import clickable_hint, run_operator_mode  # noqa: E402


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(clickable_hint(), flush=True)
        return 2
    return run_operator_mode(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
