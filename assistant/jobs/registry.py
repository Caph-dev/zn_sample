"""Fail-closed registry for assistant job handlers."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


JobHandler = Callable[[str, Any], str]
ZINIAO_JOB_TYPES = frozenset({"environment_check"})


class HandlerFailure(RuntimeError):
    """A handler failure with a stable machine-readable error code."""

    def __init__(self, error_code: str, summary: str) -> None:
        super().__init__(summary)
        self.error_code = error_code
        self.summary = summary


class JobCancelled(RuntimeError):
    """Raised at a safe read-only cancellation checkpoint."""


def get_handler(job_type: str) -> JobHandler | None:
    """Return the only explicitly registered handler, otherwise fail closed."""
    if job_type != "environment_check":
        return None
    from assistant.jobs.handlers.environment_check import run_environment_check

    return run_environment_check
