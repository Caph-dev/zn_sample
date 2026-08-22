"""Beijing-time helpers for follow-up domain calculations."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python versions without zoneinfo
    ZoneInfo = None  # type: ignore[assignment,misc]


try:
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo unavailable")
    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:
    # China does not observe daylight saving time, so UTC+8 is a safe fallback.
    BEIJING = timezone(timedelta(hours=8))


def beijing_now(now: datetime | None = None) -> datetime:
    """Return a timezone-aware Beijing datetime.

    Naive inputs are interpreted as Beijing wall-clock time rather than UTC.
    """
    if now is None:
        return datetime.now(BEIJING)
    if now.tzinfo is None:
        return now.replace(tzinfo=BEIJING)
    return now.astimezone(BEIJING)


def beijing_date(datetime_value: datetime) -> date:
    """Return the Beijing natural date containing ``datetime_value``."""
    return beijing_now(datetime_value).date()
