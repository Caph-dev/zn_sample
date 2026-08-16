"""终端详略。默认只打业务员能看懂的进度；--verbose 才打 API 明细。"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_verbose = False
_last_progress_cols = 0


def _visual_cols(text: str) -> int:
    width = 0
    for char in text:
        if ord(char) > 127:
            width += 2
        else:
            width += 1
    return width


def set_verbose(enabled: bool) -> None:
    global _verbose
    _verbose = bool(enabled)


def is_verbose() -> bool:
    return _verbose


def verbose_print(*args: object, **kwargs: object) -> None:
    if _verbose:
        logger.debug("%s", " ".join(str(item) for item in args))


def progress_line(text: str) -> None:
    global _last_progress_cols
    pad = max(0, _last_progress_cols - _visual_cols(text))
    sys.stdout.write("\r" + text + (" " * pad))
    sys.stdout.flush()
    _last_progress_cols = max(_last_progress_cols, _visual_cols(text))


def progress_done(text: str) -> None:
    global _last_progress_cols
    pad = max(0, _last_progress_cols - _visual_cols(text))
    sys.stdout.write("\r" + text + (" " * pad) + "\n")
    sys.stdout.flush()
    _last_progress_cols = 0
