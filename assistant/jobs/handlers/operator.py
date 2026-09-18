"""Web adapter for the four fixed, cross-platform operator launch modes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

from assistant.database.models import Job
from assistant.jobs.locks import cancel_flag_path
from assistant.jobs.progress import update_progress
from assistant.jobs.registry import HandlerFailure, JobCancelled
from assistant.paths import ensure_user_dirs
from lib.job_cancel import CANCEL_FLAG_ENV, EXIT_CODE_CANCELLED
from lib.operator_launch import (
    MODES,
    PLATFORM_WAIT_SECONDS,
    choose_report,
    default_out_prefix,
    pipeline_prefixes,
    run_operator_mode,
)


JOB_MODE_KEYS = {
    "operator_prepare": "prepare",
    "operator_screen": "screen",
    "operator_pipeline": "pipeline",
    "operator_tracking": "tracking",
}


def _load_job_request(session_factory, job_id: str) -> tuple[str, dict]:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HandlerFailure("operator-job-missing", "操作任务不存在。")
        job_type = str(job.job_type)
        raw_request = str(job.result_summary or "").strip()
    try:
        request_payload = json.loads(raw_request) if raw_request else {}
    except json.JSONDecodeError as error:
        raise HandlerFailure(
            "operator-request-invalid",
            "操作任务参数无法读取，已拒绝执行。",
        ) from error
    if not isinstance(request_payload, dict):
        raise HandlerFailure(
            "operator-request-invalid",
            "操作任务参数格式不正确，已拒绝执行。",
        )
    return job_type, request_payload


def _save_log_path(session_factory, job_id: str, log_path: Path) -> None:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.log_path = str(log_path)
        session.commit()


def _run_subprocess(
    argv: Sequence[str],
    *,
    log_path: Path,
    cancel_flag: Path | None = None,
) -> int:
    """Run one fixed argv without a shell on macOS or Windows.

    ``cancel_flag`` is handed to the child as a sentinel path; the child stops
    at its own safe checkpoint instead of being killed mid-write.
    """
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    if cancel_flag is not None:
        environment[CANCEL_FLAG_ENV] = str(cancel_flag)
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if sys.platform == "win32"
        else 0
    )
    with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
        completed_process = subprocess.run(
            list(argv),
            cwd=str(Path(__file__).resolve().parents[3]),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=environment,
            shell=False,
            check=False,
            creationflags=creation_flags,
        )
    return int(completed_process.returncode)


def _resolve_report_name(mode_key: str, started_at: datetime) -> str:
    if mode_key == "pipeline":
        prefixes = pipeline_prefixes(now=started_at)
        for prefix_name in ("confirm", "approve", "screen"):
            report_path = choose_report(prefixes[prefix_name])
            if report_path is not None:
                return report_path.name
        return ""
    mode = MODES[mode_key]
    if not mode.writes_report:
        return ""
    report_path = choose_report(default_out_prefix(mode, now=started_at))
    return report_path.name if report_path is not None else ""


def run_operator_job(job_id: str, session_factory) -> str:
    """Execute one allowlisted launcher mode through its existing orchestrator."""
    job_type, request_payload = _load_job_request(session_factory, job_id)
    mode_key = JOB_MODE_KEYS.get(job_type)
    if mode_key is None:
        raise HandlerFailure(
            "operator-mode-not-allowed",
            "该网页操作没有登记，已拒绝执行。",
        )

    started_at = datetime.now()
    total_steps = 6 if mode_key == "pipeline" else 3
    progress_current = 1
    log_directory = ensure_user_dirs() / "logs"
    log_path = log_directory / (
        f"operator_{mode_key}_{started_at.strftime('%Y%m%d_%H%M%S')}.log"
    )
    _save_log_path(session_factory, job_id, log_path)

    # 物流任务是长循环写操作：取消走哨兵文件，子进程在每一行的边界停下，
    # 已经写入飞书、已经发出的私信都保留，绝不从写入中间杀进程。
    cancel_flag = cancel_flag_path(job_id) if mode_key == "tracking" else None
    if cancel_flag is not None:
        cancel_flag.unlink(missing_ok=True)

    def emit_progress(message: str) -> None:
        nonlocal progress_current
        normalized_message = str(message or "").strip()
        if not normalized_message:
            return
        if (
            cancel_flag is not None
            and cancel_flag.is_file()
            and normalized_message.startswith("没有跑完")
        ):
            normalized_message = (
                "已请求取消：已在安全检查点停下，已写入的飞书行和已发私信保留"
            )
        if normalized_message.startswith("第 2 步"):
            progress_current = max(progress_current, 2)
        elif normalized_message.startswith("10 分钟已到"):
            progress_current = max(progress_current, 4)
        elif normalized_message.startswith("第 3 步"):
            progress_current = max(progress_current, 4)
        elif normalized_message.startswith("第 4 步"):
            progress_current = max(progress_current, 5)
        update_progress(
            session_factory,
            job_id,
            current=progress_current,
            total=total_steps,
            message=normalized_message,
        )

    def wait_for_platform_refresh() -> None:
        nonlocal progress_current
        progress_current = max(progress_current, 3)
        for elapsed_seconds in range(PLATFORM_WAIT_SECONDS):
            if elapsed_seconds % 30 == 0:
                remaining_seconds = PLATFORM_WAIT_SECONDS - elapsed_seconds
                update_progress(
                    session_factory,
                    job_id,
                    current=progress_current,
                    total=total_steps,
                    message=f"等待平台刷新，约剩 {remaining_seconds} 秒",
                )
            time.sleep(1)

    update_progress(
        session_factory,
        job_id,
        current=progress_current,
        total=total_steps,
        message=f"正在启动：{MODES[mode_key].title}",
    )

    if cancel_flag is not None and cancel_flag.is_file():
        raise JobCancelled(
            "任务在启动前被取消；本轮没有写飞书，也没有发私信。",
            result_summary=json.dumps(
                {
                    "mode": mode_key,
                    "summary": "启动前已取消：没有写飞书，也没有发私信。",
                    "report_name": "",
                    "force_used": bool(request_payload.get("force", False)),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def run_fixed_argv(argv: Sequence[str]) -> int:
        return _run_subprocess(argv, log_path=log_path, cancel_flag=cancel_flag)

    force_requested = bool(request_payload.get("force", False))
    return_code = run_operator_mode(
        mode_key,
        python=sys.executable,
        now=started_at,
        confirm_fn=lambda _mode: True,
        force_fn=lambda: force_requested,
        run_job_fn=run_fixed_argv,
        wait_fn=wait_for_platform_refresh,
        printer=emit_progress,
    )
    if return_code == EXIT_CODE_CANCELLED:
        report_name = _resolve_report_name(mode_key, started_at)
        raise JobCancelled(
            "物流任务已在安全检查点取消。",
            result_summary=json.dumps(
                {
                    "mode": mode_key,
                    "summary": (
                        "已在安全检查点取消：已写入的飞书行和已发私信保留，"
                        "未处理的行没有写入；下次再跑即可接着处理。"
                    ),
                    "report_name": report_name,
                    "force_used": force_requested,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    if return_code != 0:
        raise HandlerFailure(
            f"operator-{mode_key}-failed",
            f"{MODES[mode_key].title}没有跑完（代号 {return_code}）。请查看任务日志。",
        )

    update_progress(
        session_factory,
        job_id,
        current=total_steps,
        total=total_steps,
        message=f"{MODES[mode_key].title}已完成",
    )
    report_name = _resolve_report_name(mode_key, started_at)
    return json.dumps(
        {
            "mode": mode_key,
            "summary": f"{MODES[mode_key].title}已完成",
            "report_name": report_name,
            "force_used": force_requested,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
