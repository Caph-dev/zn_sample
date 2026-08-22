"""Fail-closed registry for assistant job handlers."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


JobHandler = Callable[[str, Any], str]
ZINIAO_JOB_TYPES = frozenset({"environment_check", "shipment_sync"})
REGISTERED_JOB_TYPES = frozenset(
    {"environment_check", "shipment_sync", "followup_generate", "report_export"}
)


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
    if job_type == "environment_check":
        from assistant.jobs.handlers.environment_check import run_environment_check
        return run_environment_check
    if job_type == "shipment_sync":
        from assistant.jobs.handlers.shipment_sync import run_shipment_sync
        return run_shipment_sync
    if job_type == "followup_generate":
        from assistant.jobs.handlers.followup_generate import run_followup_generate
        return run_followup_generate
    if job_type == "report_export":
        from assistant.jobs.handlers.report_export import run_report_export
        return run_report_export
    return None
