"""Temporary debug instrumentation for session 469bfe."""
from __future__ import annotations

import json
import threading
from pathlib import Path

_LOG_PATH = Path("/Users/Caph/Projects/zn_sample/.cursor/debug-469bfe.log")
_WRITE_LOCK = threading.Lock()


def debug_log(event: str, *, location: str, message: str = "", **data) -> None:
    record = {
        "sessionId": "469bfe",
        "location": location,
        "event": event,
        "message": message,
        "data": data,
    }
    with _WRITE_LOCK:
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
