"""Custom SQLAlchemy column types for persisted business values."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Persist logistics timestamps as UTC and return timezone-aware UTC values.

    SQLite strips timezone information from ``DateTime`` values. Logistics API
    timestamps are aware UTC values, so this type stores their UTC wall-clock
    value and restores ``timezone.utc`` when reading it back. Naive inputs keep
    their existing wall-clock value for compatibility with existing callers.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value

        utc_value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return utc_value.replace(tzinfo=None)
        return utc_value

    def process_result_value(
        self,
        value: datetime | None,
        dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
