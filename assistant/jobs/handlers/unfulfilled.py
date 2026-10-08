"""Fixed D+15 Feishu write job with frozen scope and durable failure reports."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from assistant.database.models import Job
from assistant.jobs.registry import HandlerFailure
from assistant.paths import (
    application_resource_dir,
    assert_private_interpreter,
    ensure_user_dirs,
    python_subprocess_environment,
    user_exports_dir,
)


JOB_TYPE = "followup_unfulfilled_write"
REQUEST_KEYS = frozenset({"task_ids", "store_id", "execute_limit"})
SUMMARY_KEYS = REQUEST_KEYS | frozenset({
    "mode", "rows", "counts", "json_path", "csv_path", "backup_path", "stopped_reason",
})
ARTIFACT_FAILURE_REASONS = frozenset({
    "artifact-failed-before-write", "artifact-failed-after-row",
})


def unfulfilled_report_path(job_id: str) -> Path:
    """Shared script/handler contract: job-mode JSON has this fixed location."""
    if not job_id or any(
        not character.isascii() or not (character.isalnum() or character in "_-")
        for character in job_id
    ):
        raise ValueError("Invalid job ID")
    return user_exports_dir() / "followup_unfulfilled" / f"{job_id}_result.json"


def _load_request(session_factory, job_id: str) -> dict:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None or job.job_type != JOB_TYPE or job.status != "running":
            raise HandlerFailure("unfulfilled-job-invalid", "The D+15 write job is not running.")
        try:
            request_payload = json.loads(job.result_summary)
        except (TypeError, ValueError) as error:
            raise HandlerFailure("unfulfilled-request-invalid", "Cannot read the frozen D+15 request.") from error
        if not isinstance(request_payload, dict) or set(request_payload) != REQUEST_KEYS:
            raise HandlerFailure("unfulfilled-request-invalid", "Invalid frozen D+15 request fields.")
        task_ids = request_payload["task_ids"]
        execute_limit = request_payload["execute_limit"]
        store_id = request_payload["store_id"]
        if (not isinstance(task_ids, list) or not task_ids
                or any(type(task_id) is not int or task_id <= 0 for task_id in task_ids)
                or len(set(task_ids)) != len(task_ids)
                or type(execute_limit) is not int or execute_limit <= 0
                or len(task_ids) > execute_limit
                or not isinstance(store_id, str) or not store_id.strip()
                or store_id != job.store_id):
            raise HandlerFailure("unfulfilled-request-invalid", "Invalid frozen D+15 scope or limit.")
        return request_payload


def _fixed_argv(job_id: str, request_payload: dict) -> list[str]:
    assert_private_interpreter()
    return [
        sys.executable,
        str(application_resource_dir() / "scripts" / "mark_unfulfilled_followups.py"),
        "--job-id", job_id,
        "--execute", "--yes", "--write-feishu",
        "--execute-limit", str(request_payload["execute_limit"]),
    ]


def _read_report(report_path: Path, request_payload: dict) -> dict | None:
    """Missing JSON is normal before the script writes its first checkpoint."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise HandlerFailure("unfulfilled-report-invalid", "Cannot read the D+15 result report; review the log.") from error
    return _validate_report(report, request_payload, json_path=str(report_path))


def _validate_report(report: dict, request_payload: dict, *, json_path: str) -> dict:
    if (not isinstance(report, dict)
            or any(report.get(key) != request_payload[key] for key in REQUEST_KEYS)
            or report.get("json_path") != json_path
            or report.get("mode") != JOB_TYPE
            or not isinstance(report.get("stopped_reason"), str)
            or not isinstance(report.get("csv_path"), str)
            or not isinstance(report.get("backup_path"), str)
            or not isinstance(report.get("rows"), list)
            or any(not isinstance(row, dict) for row in report["rows"])
            or not isinstance(report.get("counts"), dict)):
        raise HandlerFailure("unfulfilled-report-invalid", "The D+15 report does not match the frozen request.")
    return {key: report[key] for key in SUMMARY_KEYS}


def _read_artifact_failure(session_factory, job_id: str, request_payload: dict) -> dict | None:
    """After child exit, prefer its latest DB outcomes over a stale file checkpoint."""
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None or job.job_type != JOB_TYPE or job.store_id != request_payload["store_id"]:
            return None
        try:
            summary = json.loads(job.result_summary)
        except (TypeError, ValueError):
            return None
    if (not isinstance(summary, dict)
            or summary.get("stopped_reason") not in ARTIFACT_FAILURE_REASONS
            or summary.get("json_path") != "" or summary.get("csv_path") != ""):
        return None
    try:
        return _validate_report(summary, request_payload, json_path="")
    except HandlerFailure:
        return None


def _persist_report(session_factory, job_id: str, report: dict) -> str:
    result_summary = json.dumps(report, ensure_ascii=False, sort_keys=True)
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.result_summary = result_summary
            session.commit()
    return result_summary


def run_unfulfilled_write(job_id: str, session_factory) -> str:
    """Run one fixed script; never terminate an in-flight remote write."""
    request_payload = _load_request(session_factory, job_id)
    report_path = unfulfilled_report_path(job_id)
    argv = _fixed_argv(job_id, request_payload)
    log_path = ensure_user_dirs() / "logs" / f"followup_unfulfilled_{job_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with session_factory() as session:
        job = session.get(Job, job_id)
        job.log_path = str(log_path)
        session.commit()
    environment = python_subprocess_environment()
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
        process_handle = subprocess.Popen(
            argv,
            cwd=str(application_resource_dir()),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=environment,
            shell=False,
            creationflags=creation_flags,
        )
        # The script alone publishes row progress and checkpoints. Wait without
        # duplicating events or allowing report errors to abandon a live writer.
        return_code = int(process_handle.wait())

    report = _read_artifact_failure(session_factory, job_id, request_payload)
    if report is None:
        report = _read_report(report_path, request_payload)
    result_summary = _persist_report(session_factory, job_id, report) if report else ""
    if return_code != 0 or (report and report["stopped_reason"] in ARTIFACT_FAILURE_REASONS):
        raise HandlerFailure(
            f"unfulfilled-write-failed-{return_code}",
            f"D+15 updates stopped (exit {return_code}); review the saved rows and log before any further action.",
        )
    if report is None:
        raise HandlerFailure("unfulfilled-report-missing", "D+15 script exited without a result report; manual review is required.")
    return result_summary
