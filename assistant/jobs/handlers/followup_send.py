"""Web adapter for sending one due follow-up message through the fixed script."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from assistant.database.models import Job
from assistant.jobs.progress import append_event, update_progress
from assistant.jobs.registry import HandlerFailure
from assistant.paths import ensure_user_dirs


def _load_task_id(session_factory, job_id: str) -> int:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HandlerFailure("followup-send-job-missing", "发送任务不存在。")
        raw_request = str(job.result_summary or "").strip()
    try:
        request_payload = json.loads(raw_request) if raw_request else {}
    except json.JSONDecodeError as error:
        raise HandlerFailure(
            "followup-send-request-invalid",
            "发送任务参数无法读取，已拒绝执行。",
        ) from error
    if not isinstance(request_payload, dict):
        raise HandlerFailure(
            "followup-send-request-invalid",
            "发送任务参数格式不正确，已拒绝执行。",
        )
    try:
        task_id = int(request_payload.get("task_id"))
    except (TypeError, ValueError) as error:
        raise HandlerFailure(
            "followup-send-request-invalid",
            "发送任务缺少 task_id，已拒绝执行。",
        ) from error
    return task_id


def _run_script(*, task_id: int, job_id: str, log_path: Path) -> int:
    """Run one fixed argv without a shell on macOS or Windows."""
    root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if sys.platform == "win32"
        else 0
    )
    argv = [
        sys.executable,
        str(root / "scripts" / "send_followup_message.py"),
        "--execute",
        "--yes",
        "--task-id",
        str(task_id),
        "--ignore-job-id",
        job_id,
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
        completed_process = subprocess.run(
            argv,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=environment,
            shell=False,
            check=False,
            creationflags=creation_flags,
        )
    return int(completed_process.returncode)


def run_followup_send(job_id: str, session_factory) -> str:
    """Send exactly one task; the script keeps every gate and re-validates it."""
    task_id = _load_task_id(session_factory, job_id)
    update_progress(
        session_factory,
        job_id,
        current=0,
        total=2,
        message="准备发送跟进私信",
    )
    log_path = ensure_user_dirs() / "logs" / (
        f"followup_send_{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.log_path = str(log_path)
            session.commit()

    return_code = _run_script(task_id=task_id, job_id=job_id, log_path=log_path)
    if return_code != 0:
        raise HandlerFailure(
            f"followup-send-failed-{return_code}",
            f"跟进私信没有发送成功（代号 {return_code}）。请查看任务日志。",
        )

    update_progress(
        session_factory,
        job_id,
        current=2,
        total=2,
        message="跟进私信已发送",
    )
    append_event(
        session_factory,
        job_id,
        level="info",
        event_type="job.stage",
        message="跟进私信已发送",
    )
    return json.dumps(
        {"mode": "followup_send", "task_id": task_id},
        ensure_ascii=False,
        sort_keys=True,
    )
