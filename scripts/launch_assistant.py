#!/usr/bin/env python3
"""Launch the localhost-only sample follow-up assistant.

默认先停止仍在运行的旧实例再启动（保证加载最新代码）。运行中的网页写
任务（operator_*）会阻止自动重启，避免中断平台批准、飞书写入或私信。
``--no-restart`` 保留旧行为：旧实例存在时只打开浏览器。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from assistant import bootstrap
from assistant.paths import database_path, ensure_user_dirs, runtime_dir
from assistant.settings import APP_NAME, BIND_HOST

# 网页 0/1/2/3 入口对应的任务：运行中不允许被自动重启打断。
PROTECTED_JOB_TYPES = frozenset(
    {
        "operator_prepare",
        "operator_screen",
        "operator_pipeline",
        "operator_tracking",
        "auto_approval_execute",
        "auto_approval_reconcile",
    }
)
STOP_WAIT_SECONDS = 5.0


def _is_process_alive(process_id: int) -> bool:
    """系统确认进程已消失才返回 False；无法确认时按存活处理。"""
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, OSError):
        return True
    return True


def _read_runtime_state() -> dict | None:
    state_path = runtime_dir() / "state.json"
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _health_check(port: int, *, timeout_seconds: float = 2.0) -> bool:
    """确认该端口上确实是本助手在提供服务。"""
    health_url = f"http://{BIND_HOST}:{port}/api/health"
    try:
        with urllib.request.urlopen(health_url, timeout=timeout_seconds) as response:
            health = json.load(response)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return health.get("app") == APP_NAME


def _existing_instance(state: dict | None) -> tuple[int, int] | None:
    """返回 (pid, port)：state.json 里的进程仍存活且健康检查确认是本助手。"""
    if not state:
        return None
    try:
        pid = int(state.get("pid") or 0)
        port = int(state.get("port") or 0)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or port <= 0:
        return None
    if not _is_process_alive(pid):
        return None
    if not _health_check(port):
        return None
    return pid, port


def _running_job_types() -> list[str]:
    database = database_path()
    if not database.exists():
        return []
    try:
        connection = sqlite3.connect(database, timeout=5)
        try:
            rows = connection.execute(
                "SELECT job_type FROM jobs WHERE status = 'running'"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    return [str(row[0]) for row in rows]


def _mark_interrupted_running_jobs(job_types: list[str]) -> None:
    """重启打断的只读任务标记为失败，避免下次启动卡在孤儿 running 任务。"""
    if not job_types:
        return
    finished_at = datetime.now(timezone.utc).isoformat()
    try:
        connection = sqlite3.connect(database_path(), timeout=5)
        try:
            connection.execute(
                "UPDATE jobs SET status = 'failed', finished_at = ?, "
                "error_code = 'restarted', "
                "error_summary = '操作台重启，任务被中断' "
                "WHERE status = 'running'",
                (finished_at,),
            )
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error as error:
        print(f"警告：未能标记被中断的任务：{error}", file=sys.stderr)


def _terminate_process(pid: int) -> bool:
    """优雅终止旧实例，超时后升级为强杀。"""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False

    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _is_process_alive(pid):
            return True
        time.sleep(0.2)

    if sys.platform != "win32":
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        time.sleep(0.5)
    return not _is_process_alive(pid)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本地样品跟进操作台。")
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="旧实例存在时只打开浏览器，不自动重启",
    )
    arguments = parser.parse_args()

    ensure_user_dirs()
    existing = _existing_instance(_read_runtime_state())
    if existing is not None and not arguments.no_restart:
        pid, port = existing
        running_jobs = _running_job_types()
        protected_jobs = [
            job_type for job_type in running_jobs if job_type in PROTECTED_JOB_TYPES
        ]
        if protected_jobs:
            print(
                "操作台有运行中的写任务"
                f"（{', '.join(protected_jobs)}），为避免中断平台写操作，不自动重启。",
                file=sys.stderr,
            )
            print(
                "请等任务结束后再启动，或确认安全后手动 kill 旧进程。",
                file=sys.stderr,
            )
            return 2
        if running_jobs:
            print(
                f"检测到运行中的只读任务（{', '.join(running_jobs)}），"
                "将随重启中断并标记为失败。"
            )
        print(
            f"检测到旧版操作台进程 (pid={pid}, port={port})，"
            "正在停止并重启以加载最新代码…"
        )
        if not _terminate_process(pid):
            print(f"旧进程 (pid={pid}) 未能停止，请手动 kill 后重试。", file=sys.stderr)
            return 1
        _mark_interrupted_running_jobs(running_jobs)
        print("旧进程已停止，启动最新版本…")

    return bootstrap.main()


if __name__ == "__main__":
    raise SystemExit(main())
