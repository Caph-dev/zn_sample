"""Run only the server-owned cleanup batch bound to this durable Job.

The worker provides the heartbeat; release tracking wraps subprocess.run's
Popen globally. No cancellation, subprocess termination or write retry lives here.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from assistant.database.models import Job, TargetCleanupBatch
from assistant.jobs.progress import update_progress
from assistant.jobs.registry import HandlerFailure
from assistant.paths import (
    application_resource_dir,
    assert_private_interpreter,
    ensure_user_dirs,
    python_subprocess_environment,
)
from assistant.services import target_cleanup_service as service
from assistant.services.page_lock import blocking_jobs

logger = logging.getLogger(__name__)
JOB_TYPE_TO_MODE = {
    service.PREVIEW_JOB_TYPE: "preview",
    service.EXECUTE_JOB_TYPE: "execute",
}


@dataclass(frozen=True)
class BoundCleanupJob:
    batch_id: str
    mode: str
    job_status: str
    job_store_id: str | None
    batch_store_id: str
    phase_status: str
    candidate_count: int

    @property
    def batch_url(self) -> str:
        return f"/plan-cleanup?batch_id={self.batch_id}"


def _require_identifier(identifier: str) -> None:
    try:
        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError("Canonical UUID required")
    except (ValueError, TypeError, AttributeError) as error:
        raise HandlerFailure("target-cleanup-binding-invalid", "Cleanup requires server-generated UUID bindings.") from error


def _load_binding(session_factory, job_id: str) -> BoundCleanupJob:
    _require_identifier(job_id)
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HandlerFailure("target-cleanup-job-missing", "Cleanup job does not exist.")
        mode = JOB_TYPE_TO_MODE.get(job.job_type)
        if mode is None:
            raise HandlerFailure("target-cleanup-mode-not-allowed", "Cleanup job type is not registered.")
        batches = session.scalars(select(TargetCleanupBatch).where(
            getattr(TargetCleanupBatch, f"{mode}_job_id") == job_id,
        ).limit(2)).all()
        if len(batches) != 1:
            raise HandlerFailure("target-cleanup-binding-invalid", "Cleanup job must bind exactly one domain batch.")
        batch = batches[0]
        _require_identifier(batch.id)
        # result_summary is worker-owned output, never an input or batch locator.
        return BoundCleanupJob(
            batch.id, mode, job.status, job.store_id, batch.store_id,
            getattr(batch, f"{mode}_status"), batch.candidate_count,
        )


def _failure(binding: BoundCleanupJob, code: str, summary: str) -> HandlerFailure:
    return HandlerFailure(code, f"{summary} Review batch: {binding.batch_url}")


def _completion_message(binding: BoundCleanupJob, payload: dict) -> str:
    if binding.mode == "preview":
        return f"Read-only preview completed: {payload['candidate_count']} candidates."
    counts = payload["counts"]
    return (f"Execution ended: {counts['submitted']} cancellation operations submitted; "
            "this is not platform-final cancellation confirmation.")


def run_target_cleanup_job(job_id: str, session_factory) -> str:
    try:
        binding = _load_binding(session_factory, job_id)
    except HandlerFailure:
        raise
    except Exception as error:
        raise HandlerFailure(
            "target-cleanup-binding-unavailable",
            "Cleanup binding could not be read; no child was started. Review /plan-cleanup and the task log.",
        ) from error
    # A duplicate handler invocation does not own the other child's claim.
    if binding.phase_status != "queued":
        raise _failure(binding, "batch-already-claimed", "Cleanup phase is not queued; no automatic retry is allowed.")
    try:
        if binding.job_status != "running":
            raise _failure(binding, "job-claim-required", "Cleanup job must already be claimed and running.")
        if not binding.batch_store_id or binding.job_store_id != binding.batch_store_id:
            raise _failure(binding, "store-binding-mismatch", "Cleanup job and batch store bindings do not match.")
        if blocking_jobs(session_factory, ignore_job_id=job_id):
            raise _failure(binding, "store-busy", "Another pending or running task occupies the store page.")

        assert_private_interpreter()
        resource_directory = application_resource_dir()
        argv = [
            sys.executable, str(resource_directory / "scripts" / "cleanup_target_plans.py"),
            "--mode", binding.mode, "--batch-id", binding.batch_id, "--job-id", job_id,
        ]
        if binding.mode == "execute":
            argv.extend(["--execute", "--yes"])
        log_path = ensure_user_dirs() / "logs" / f"target_cleanup_{binding.mode}_{job_id}.log"
        with session_factory() as session:
            session.get(Job, job_id).log_path = str(log_path)
            session.commit()
        update_progress(
            session_factory, job_id, current=0,
            total=max(1, binding.candidate_count) if binding.mode == "execute" else 1,
            message=f"Starting cleanup {binding.mode}. Batch: {binding.batch_url}",
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
            completed_process = subprocess.run(
                argv, cwd=str(resource_directory), stdin=subprocess.DEVNULL,
                stdout=log_file, stderr=subprocess.STDOUT,
                env=python_subprocess_environment(), shell=False, check=False,
                creationflags=creation_flags,
            )
        return_code = int(completed_process.returncode)
        if binding.mode == "execute":
            service.finish_execution(
                session_factory, binding.batch_id,
                status="completed" if return_code == 0 else "failed",
                error="" if return_code == 0 else f"Cleanup subprocess exited with code {return_code}; no retry.",
            )
        payload = service.batch_payload(session_factory, binding.batch_id, limit=1)
        counts = payload["counts"]
        phase_status = payload[f"{binding.mode}_status"]
        complete = phase_status == "completed"
        if binding.mode == "execute":
            complete = (complete and payload["total"] > 0
                        and counts["submitted"] == payload["total"]
                        and all(count == 0 for status, count in counts.items() if status != "submitted"))
        if return_code != 0 or not complete:
            summary = (f"Cleanup {binding.mode} did not complete (exit {return_code}; "
                       f"domain {phase_status}; counts {json.dumps(counts, sort_keys=True)}). "
                       "Known results are retained; no automatic retry. See the batch and task log.")
            raise _failure(binding, f"target-cleanup-{binding.mode}-incomplete", summary)
        message = _completion_message(binding, payload)
        update_progress(
            session_factory, job_id, current=payload["total"] if binding.mode == "execute" else 1,
            total=payload["total"] if binding.mode == "execute" else 1,
            message=f"{message} Batch: {binding.batch_url}",
        )
        return json.dumps({
            "batch_id": binding.batch_id, "batch_url": binding.batch_url, "mode": binding.mode,
            "message": message, "counts": counts, "downloads": payload["downloads"],
            "preview_status": payload["preview_status"], "execute_status": payload["execute_status"],
        }, sort_keys=True)
    except Exception as error:
        if isinstance(error, HandlerFailure):
            failure = error
        elif isinstance(error, service.TargetCleanupServiceError):
            failure = _failure(binding, error.code, error.summary)
        else:
            # Subprocess/OS exceptions may contain argv or environment details.
            failure = _failure(binding, "target-cleanup-handler-failed",
                               "Cleanup could not complete. Known evidence is retained; inspect the task log. No retry.")
        try:
            service.fail_batch(session_factory, binding.batch_id, binding.mode, failure.summary, code=failure.error_code)
        except Exception:
            logger.warning("Cleanup batch synchronization requires review: batch=%s", binding.batch_id)
            failure = _failure(binding, "target-cleanup-sync-failed",
                               "Cleanup stopped and evidence synchronization requires manual review. No retry.")
        try:
            payload = service.batch_payload(session_factory, binding.batch_id, limit=1)
            processed_count = sum(payload["counts"][status] for status in ("submitted", "skipped", "failed", "uncertain"))
            update_progress(
                session_factory, job_id, current=processed_count if binding.mode == "execute" else 0,
                total=max(1, payload["total"]) if binding.mode == "execute" else 1,
                message=failure.summary,
            )
        except Exception:
            logger.warning("Cleanup final progress unavailable: batch=%s", binding.batch_id)
        raise failure from error
