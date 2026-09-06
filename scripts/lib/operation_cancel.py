"""Shared cancellation signal for long-running read-only browser operations."""
from __future__ import annotations

from collections.abc import Callable


class OperationCancelled(RuntimeError):
    """Raised when a browser operation reaches a cancellation checkpoint."""


def raise_if_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    """Raise outside transport exception handlers when cancellation was requested."""
    if cancel_check is not None and cancel_check():
        raise OperationCancelled("read-only operation cancelled")
