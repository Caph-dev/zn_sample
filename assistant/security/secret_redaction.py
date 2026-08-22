"""Redact credential values before display or logging."""
from __future__ import annotations

import re


SECRET_KEY_PATTERN = r"app_secret|access_token|tenant_access_token|cookie"
SECRET_VALUE_PATTERN = re.compile(
    rf"""(?ix)
    (?P<key>
        "(?:{SECRET_KEY_PATTERN})"
        | '(?:{SECRET_KEY_PATTERN})'
        | (?:{SECRET_KEY_PATTERN})
    )
    (?P<separator>\s*[:=]\s*)
    (?:
        (?P<double_quoted_value>"(?:\\.|[^"\\])*")
        | (?P<single_quoted_value>'(?:\\.|[^'\\])*')
        | (?P<unquoted_value>[^\s,;}}&)]+)
    )
    """
)


def _replace_secret_value(match: re.Match[str]) -> str:
    """Preserve structured quoting while replacing a credential value."""
    if match.group("double_quoted_value") is not None:
        redacted_value = '"[redacted]"'
    elif match.group("single_quoted_value") is not None:
        redacted_value = "'[redacted]'"
    else:
        redacted_value = "[redacted]"
    return f"{match.group('key')}{match.group('separator')}{redacted_value}"


def redact_text(value: object) -> str:
    """Replace known credential values while preserving useful context."""
    return SECRET_VALUE_PATTERN.sub(_replace_secret_value, str(value))
