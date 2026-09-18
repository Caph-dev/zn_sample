"""Non-secret settings for the local assistant."""
from __future__ import annotations

import os


APP_NAME = "ZnSampleAssistant"
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8766


def preferred_port() -> int:
    """Return the requested local port, falling back to the safe default."""
    raw_port = (os.environ.get("ZN_ASSISTANT_PORT") or "").strip()
    if not raw_port:
        return DEFAULT_PORT
    try:
        port = int(raw_port)
    except ValueError:
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT
