"""自动审批任务 handler：固定 argv 子进程，不执行网页 shell 字符串。

三种任务类型：
- auto_approval_preview   只读自定义筛查（预览）
- auto_approval_execute   限量批准 + 可选写飞书（服务端已确认 + 幂等）
- auto_approval_reconcile 核对补写（不重新批准）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from assistant.database.models import Job
from assistant.jobs.locks import is_cancellation_requested
from assistant.jobs.progress import append_event, update_progress
from assistant.jobs.registry import HandlerFailure, JobCancelled
from assistant.paths import ensure_user_dirs
from assistant.services import auto_approval_service

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
AUTO_APPROVAL_SCRIPT = REPOSITORY_ROOT / "scripts" / "auto_approval.py"

JOB_TYPE_TO_MODE = {
    "auto_approval_preview": "preview",
    "auto_approval_execute": "execute",
    "auto_approval_reconcile": "reconcile",
}

PROGRESS_POLL_SECONDS = 2.0
PROCESS_STOP_WAIT_SECONDS = 5.0


def _load_request(session_factory, job_id: str) -> tuple[str, dict]:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HandlerFailure("auto-approval-job-missing", "自动审批任务不存在。")
        job_type = str(job.job_type)
        raw_request = str(job.result_summary or "").strip()
    try:
        request_payload = json.loads(raw_request) if raw_request else {}
    except json.JSONDecodeError as error:
        raise HandlerFailure(
            "auto-approval-request-invalid",
            "自动审批任务参数无法读取，已拒绝执行。",
        ) from error
    if not isinstance(request_payload, dict):
        raise HandlerFailure(
            "auto-approval-request-invalid",
            "自动审批任务参数格式不正确，已拒绝执行。",
        )
    return job_type, request_payload


def _fixed_argv(request_payload: dict, mode: str) -> list[str]:
    """组装固定 argv；只接受服务端写入的绝对路径与整数开关。"""
    rules_path = str(request_payload.get("rules_path") or "").strip()
    result_path = str(request_payload.get("result_path") or "").strip()
    if not rules_path or not result_path:
        raise HandlerFailure(
            "auto-approval-request-invalid",
            "自动审批任务缺少规则或结果路径，已拒绝执行。",
        )
    store_id = str(request_payload.get("store_id") or "").strip()
    if not store_id:
        raise HandlerFailure(
            "auto-approval-store-missing",
            "自动审批任务缺少店铺，已拒绝执行。",
        )
    argv = [
        sys.executable,
        str(AUTO_APPROVAL_SCRIPT),
        "--mode",
        mode,
        "--rules",
        rules_path,
        "--store-id",
        store_id,
        "--out",
        result_path,
        "--from-seller-home",
        "--max-pages",
        "50",
    ]
    if mode == "preview":
        preview_id = str(request_payload.get("preview_id") or "")
        if not preview_id:
            raise HandlerFailure(
                "auto-approval-request-invalid",
                "预览任务缺少 preview_id，已拒绝执行。",
            )
        argv += ["--preview-id", preview_id]
    elif mode == "execute":
        for key in ("preview_path", "apply_ids_path", "backup_prefix", "execution_id"):
            value = str(request_payload.get(key) or "").strip()
            if not value:
                raise HandlerFailure(
                    "auto-approval-request-invalid",
                    f"执行任务缺少 {key}，已拒绝执行。",
                )
        argv += [
            "--preview",
            str(request_payload["preview_path"]),
            "--apply-ids",
            str(request_payload["apply_ids_path"]),
            "--backup-out",
            str(request_payload["backup_prefix"]),
            "--execution-id",
            str(request_payload["execution_id"]),
            "--limit",
            str(int(request_payload.get("limit") or 1)),
            "--write-feishu",
            "1" if bool(request_payload.get("write_feishu")) else "0",
            "--yes",
        ]
    elif mode == "reconcile":
        for key in ("preview_path", "backup_path", "execution_id"):
            value = str(request_payload.get(key) or "").strip()
            if not value:
                raise HandlerFailure(
                    "auto-approval-request-invalid",
                    f"核对任务缺少 {key}，已拒绝执行。",
                )
        argv += [
            "--preview",
            str(request_payload["preview_path"]),
            "--backup",
            str(request_payload["backup_path"]),
            "--execution-id",
            str(request_payload["execution_id"]),
            "--limit",
            str(int(request_payload.get("limit") or 1)),
            "--write-feishu",
            "1" if bool(request_payload.get("write_feishu")) else "0",
        ]
        if request_payload.get("write_feishu"):
            argv.append("--yes")
    else:
        raise HandlerFailure(
            "auto-approval-mode-not-allowed",
            f"未登记的自动审批模式: {mode}",
        )
    return argv


def _tail_log_line(log_path: Path) -> str:
    """读取日志最后一行作为进度消息（任务页面轮询展示）。"""
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as log_file:
            lines = [line.strip() for line in log_file if line.strip()]
            return lines[-1] if lines else ""
    except OSError:
        return ""


def _progress_poller(
    session_factory,
    job_id: str,
    log_path: Path,
    process_handle,
) -> None:
    """子进程运行期间轮询日志尾部，刷新进度消息与心跳。"""
    last_message = ""
    while process_handle.poll() is None:
        if is_cancellation_requested(job_id):
            return
        message = _tail_log_line(log_path)
        if message and message != last_message:
            last_message = message
            update_progress(
                session_factory,
                job_id,
                current=0,
                total=1,
                message=message,
            )
        time.sleep(PROGRESS_POLL_SECONDS)


def _stop_process(process_handle) -> None:
    """先 SIGTERM，超时再 SIGKILL。只读筛查可停；写任务不会走到这里。"""
    if process_handle.poll() is not None:
        return
    process_handle.terminate()
    try:
        process_handle.wait(timeout=PROCESS_STOP_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        process_handle.kill()
        process_handle.wait(timeout=PROCESS_STOP_WAIT_SECONDS)


def _mark_cancelled(mode: str, preview_id: str, execution_id: str, session_factory) -> None:
    if mode == "preview" and preview_id:
        auto_approval_service.sync_preview_result(
            session_factory,
            preview_id,
            status="cancelled",
            error_code="cancelled",
            error_summary="只读筛查已取消。",
        )
    elif mode == "execute" and execution_id:
        auto_approval_service.sync_execution_result(
            session_factory,
            execution_id,
            status="cancelled",
            error_code="cancelled",
            error_summary="执行任务已取消。",
        )


def run_auto_approval_job(job_id: str, session_factory) -> str:
    job_type, request_payload = _load_request(session_factory, job_id)
    mode = JOB_TYPE_TO_MODE.get(job_type)
    if mode is None:
        raise HandlerFailure(
            "auto-approval-mode-not-allowed",
            "该自动审批任务未登记，已拒绝执行。",
        )
    with session_factory() as session:
        job = session.get(Job, job_id)
        store_id = str(job.store_id or "") if job is not None else ""
    if not store_id:
        raise HandlerFailure(
            "auto-approval-store-missing",
            "自动审批任务缺少店铺，已拒绝执行。",
        )
    request_payload["store_id"] = store_id

    argv = _fixed_argv(request_payload, mode)
    started_at = datetime.now()
    log_directory = ensure_user_dirs() / "logs"
    log_path = log_directory / (
        f"auto_approval_{mode}_{started_at.strftime('%Y%m%d_%H%M%S')}.log"
    )
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.log_path = str(log_path)
            session.commit()

    update_progress(
        session_factory,
        job_id,
        current=0,
        total=1,
        message=f"正在启动：自动审批 {mode}",
    )
    append_event(
        session_factory,
        job_id,
        level="info",
        event_type="job.started",
        message=f"自动审批 {mode} 已启动",
    )

    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if sys.platform == "win32"
        else 0
    )
    with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
        process_handle = subprocess.Popen(
            argv,
            cwd=str(REPOSITORY_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=environment,
            shell=False,
            creationflags=creation_flags,
        )
    _progress_poller(session_factory, job_id, log_path, process_handle)
    preview_id = str(request_payload.get("preview_id") or "")
    execution_id = str(request_payload.get("execution_id") or "")
    if is_cancellation_requested(job_id):
        _stop_process(process_handle)
        _mark_cancelled(mode, preview_id, execution_id, session_factory)
        raise JobCancelled("cancelled")
    return_code = int(process_handle.wait())
    if return_code != 0:
        error_code = f"auto-approval-{mode}-failed"
        error_summary = (
            f"自动审批 {mode} 没有跑完（代号 {return_code}）。请查看任务日志。"
        )
        if mode == "preview" and preview_id:
            auto_approval_service.sync_preview_result(
                session_factory,
                preview_id,
                status="failed",
                error_code=error_code,
                error_summary=error_summary,
            )
        elif mode == "execute" and execution_id:
            auto_approval_service.sync_execution_result(
                session_factory,
                execution_id,
                status="failed",
                error_code=error_code,
                error_summary=error_summary,
            )
        # reconcile 失败不改写执行批次终态：执行批次本身已完成，核对可重跑。
        raise HandlerFailure(error_code, error_summary)

    if mode == "preview" and preview_id:
        auto_approval_service.sync_preview_result(
            session_factory,
            preview_id,
            status="completed",
        )
    elif mode == "execute" and execution_id:
        auto_approval_service.sync_execution_result(
            session_factory,
            execution_id,
            status="completed",
        )
    # Reconciliation reports are independent read-back evidence. They must not
    # replace the original approval outcome or change its terminal status.

    update_progress(
        session_factory,
        job_id,
        current=1,
        total=1,
        message=f"自动审批 {mode} 已完成",
    )
    return json.dumps(
        {
            "mode": mode,
            "preview_id": preview_id,
            "execution_id": execution_id,
            "result_path": str(request_payload.get("result_path") or ""),
            "write_feishu": bool(request_payload.get("write_feishu")),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
