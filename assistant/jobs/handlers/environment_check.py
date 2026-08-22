"""Read-only environment diagnostics executed by the single worker."""
from __future__ import annotations

import json
import sys

from sqlalchemy import select

from assistant.database.models import Store
from assistant.jobs.locks import is_cancellation_requested
from assistant.jobs.progress import append_event, update_progress
from assistant.jobs.registry import HandlerFailure, JobCancelled


TOTAL_STEPS = 5


def _check_cancelled(job_id: str) -> None:
    if is_cancellation_requested(job_id):
        raise JobCancelled("cancelled")


def _save_running_store(session_factory, store: dict) -> None:
    ziniao_store_id = str(store.get("storeId") or "").strip()
    if not ziniao_store_id:
        return
    with session_factory() as session:
        existing = session.scalar(
            select(Store).where(Store.ziniao_store_id == ziniao_store_id)
        )
        if existing is None:
            session.add(
                Store(
                    ziniao_store_id=ziniao_store_id,
                    store_name=str(store.get("storeName") or ""),
                )
            )
        else:
            existing.store_name = str(store.get("storeName") or "")
        session.commit()


def run_environment_check(job_id: str, session_factory) -> str:
    """Run only allowlisted, read-only local environment checks."""
    from lib.app_config import resolve_config_path
    from lib.zclaw import list_running_stores, probe_store_page
    from lib.zclaw_cli import resolve_ziniao_cli_command

    _check_cancelled(job_id)
    update_progress(
        session_factory,
        job_id,
        current=1,
        total=TOTAL_STEPS,
        message=f"Python {sys.version.split()[0]}",
    )

    _check_cancelled(job_id)
    try:
        resolve_ziniao_cli_command()
    except RuntimeError as error:
        raise HandlerFailure("cli-not-found", "紫鸟 CLI 未找到或不可用。") from error
    update_progress(
        session_factory,
        job_id,
        current=2,
        total=TOTAL_STEPS,
        message="紫鸟 CLI 可用",
    )

    _check_cancelled(job_id)
    running_stores = list_running_stores()
    store_state: dict = {"count": len(running_stores)}
    if len(running_stores) == 1:
        store = running_stores[0]
        store_state.update(
            {
                "storeId": str(store.get("storeId") or ""),
                "storeName": str(store.get("storeName") or ""),
            }
        )
        _save_running_store(session_factory, store)
    else:
        store_state["error"] = "running-not-unique"
    update_progress(
        session_factory,
        job_id,
        current=3,
        total=TOTAL_STEPS,
        message="已读取运行店铺状态",
    )

    _check_cancelled(job_id)
    config_exists = resolve_config_path() is not None
    update_progress(
        session_factory,
        job_id,
        current=4,
        total=TOTAL_STEPS,
        message="已检查本地配置文件",
    )

    _check_cancelled(job_id)
    probe_state = "not-run"
    if len(running_stores) == 1:
        try:
            probe_store_page(str(running_stores[0].get("storeId") or ""))
            probe_state = "ready"
        except Exception:
            probe_state = "warning"
            append_event(
                session_factory,
                job_id,
                level="warning",
                event_type="job.warning",
                message="店铺页面探活失败；未执行开店或切店。",
            )
    _check_cancelled(job_id)
    update_progress(
        session_factory,
        job_id,
        current=5,
        total=TOTAL_STEPS,
        message="环境检查完成",
    )
    return json.dumps(
        {
            "stores": store_state,
            "config_exists": config_exists,
            "probe": probe_state,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
