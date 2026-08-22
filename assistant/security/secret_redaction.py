"""Redact credential values before display or logging."""
from __future__ import annotations

import re


SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(app_secret|access_token|tenant_access_token|cookie)\s*[:=]\s*"
    r"([^\s,;}&]+)"
)


def redact_text(value: object) -> str:
    """Replace known credential values while preserving useful context."""
    return SECRET_VALUE_PATTERN.sub(r"\1=[redacted]", str(value))
