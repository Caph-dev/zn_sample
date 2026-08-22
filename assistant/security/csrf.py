"""CSRF and local-origin validation helpers."""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def is_local_host(host: str) -> bool:
    hostname = host.split(":", 1)[0].strip("[]").lower()
    return hostname in {"127.0.0.1", "localhost"}


def is_valid_local_origin(origin: str, port: int) -> bool:
    try:
        parsed = urlsplit(origin)
        parsed_port = parsed.port
        parsed_hostname = parsed.hostname
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed_hostname in {"127.0.0.1", "localhost"}
        and parsed_port == port
    )
