#!/usr/bin/env python3
"""子进程任务的安全检查点取消信号。

网页点「取消」时服务端只写一个哨兵文件，不杀进程；脚本在处理下一行之前
检查文件，把当前这一行做完就停。已经写入飞书、已经发出的私信都保留，
取消永远不会落在一次写入的中间。
"""
from __future__ import annotations

import os
from pathlib import Path

CANCEL_FLAG_ENV = "ZN_SAMPLE_CANCEL_FLAG"
EXIT_CODE_CANCELLED = 3


def cancellation_requested(environ: dict[str, str] | None = None) -> bool:
    """True when the web job asked this process to stop at the next checkpoint."""
    source = os.environ if environ is None else environ
    raw_path = str(source.get(CANCEL_FLAG_ENV) or "").strip()
    if not raw_path:
        return False
    return Path(raw_path).is_file()
