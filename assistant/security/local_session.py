"""Single-use bootstrap and signed local session cookies."""
from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from assistant.security.csrf import generate_csrf_token


class LocalSessionManager:
    def __init__(self, runtime_directory: Path) -> None:
        self.runtime_directory = runtime_directory
        self.signer = TimestampSigner(secrets.token_urlsafe(32))
        self.bootstrap_token: str | None = None

    @property
    def bootstrap_path(self) -> Path:
        return self.runtime_directory / "bootstrap_token"

    def issue_bootstrap_token(self) -> str:
        self.runtime_directory.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        self.bootstrap_token = token
        self.bootstrap_path.write_text(token, encoding="utf-8")
        os.chmod(self.bootstrap_path, 0o600)
        return token

    def consume_bootstrap_token(self, candidate: str) -> str | None:
        if not self.bootstrap_token or not secrets.compare_digest(
            self.bootstrap_token, candidate
        ):
            return None
        self.bootstrap_token = None
        self.bootstrap_path.unlink(missing_ok=True)
        payload = json.dumps({"csrf": generate_csrf_token()}).encode()
        encoded_payload = base64.urlsafe_b64encode(payload).rstrip(b"=")
        return self.signer.sign(encoded_payload).decode()

    def read_session(self, cookie_value: str | None, *, max_age: int = 86400) -> dict | None:
        if not cookie_value:
            return None
        try:
            encoded_payload = self.signer.unsign(cookie_value, max_age=max_age)
            padding = b"=" * (-len(encoded_payload) % 4)
            parsed = json.loads(
                base64.urlsafe_b64decode(encoded_payload + padding).decode()
            )
        except (
            BadSignature,
            SignatureExpired,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return None
        return parsed if isinstance(parsed, dict) and parsed.get("csrf") else None

    def cleanup(self) -> None:
        self.bootstrap_token = None
        self.bootstrap_path.unlink(missing_ok=True)
