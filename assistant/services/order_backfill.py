"""订单号补写（「核对与补写」页）：飞书近期缺订单号 → 平台待发货订单号。

与按批次核对的区别：
- 不选批次、不需要先跑只读核对；规则固定为
  「人员=王良希（技术） + 系统创建时间近 72 小时 + 订单号为空」，
  候选集合每次由飞书实时判定，页面只展示、不提交任意目标。
- 仍然只写飞书「订单号」一列：不新建记录、不改合作状态/物流、不重新批准。
- 写操作沿用同一门闩：键入 y + 固定 argv 子进程任务 + 店铺页面互斥 + 任务去重。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from assistant.database.models import Job
from assistant.services import auto_approval_service as approval
from assistant.services.page_lock import blocking_jobs

ORDER_BACKFILL_JOB_TYPE = "auto_approval_order_backfill"
ORDER_BACKFILL_PERSON = "王良希（技术）"
ORDER_BACKFILL_LOOKBACK_HOURS = 72
# 0 = 不限量：候选集合由飞书实时判定且有界，重复运行幂等。
ORDER_BACKFILL_LIMIT_DEFAULT = 0
REPORT_ITEM_LIMIT = 500


def _latest_job(session) -> Job | None:
    return session.scalars(
        select(Job)
        .where(Job.job_type == ORDER_BACKFILL_JOB_TYPE)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    ).first()


def _load_request(job: Job) -> dict[str, Any]:
    try:
        payload = json.loads(job.result_summary or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_report(request: dict[str, Any]) -> dict[str, Any] | None:
    """读取脚本落盘的回读报告；结构不符一律当没有，不猜。"""
    path = str(request.get("result_path") or "").strip()
    if not path:
        return None
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(report, dict)
        or report.get("envelope_type") != "order_backfill_report"
        or not isinstance(report.get("items"), list)
    ):
        return None
    report = dict(report)
    report["items"] = [
        item for item in report["items"] if isinstance(item, dict)
    ][:REPORT_ITEM_LIMIT]
    return report


def _job_payload(job: Job | None) -> dict[str, Any]:
    if job is None:
        return {}
    return {
        "job_id": job.id,
        "status": job.status,
        "store_id": job.store_id,
        "progress_message": job.progress_message,
        "error_code": job.error_code,
        "error_summary": job.error_summary,
        "log_path": job.log_path,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def candidates_payload(*, config_path: str | None = None) -> dict[str, Any]:
    """只读扫描：按固定规则读飞书，不改任何远端状态。"""
    try:
        from lib.feishu_bitable import get_bitable_access_token
        from lib.order_backfill import collect_order_backfill_candidates

        access_token = get_bitable_access_token(config_path=config_path)
        rows = collect_order_backfill_candidates(
            access_token,
            person=ORDER_BACKFILL_PERSON,
            lookback_hours=ORDER_BACKFILL_LOOKBACK_HOURS,
        )
    except Exception as error:
        raise approval.AutoApprovalServiceError(
            "feishu-unavailable", f"飞书读取失败：{error}", 502
        ) from error
    return {
        "person": ORDER_BACKFILL_PERSON,
        "lookback_hours": ORDER_BACKFILL_LOOKBACK_HOURS,
        "limit_default": ORDER_BACKFILL_LIMIT_DEFAULT,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "total": len(rows),
        "rows": rows,
    }


def state_payload(session_factory) -> dict[str, Any]:
    """页面状态：最近任务 + 最近回读报告 + 现在能不能点补写。"""
    with session_factory() as session:
        job = _latest_job(session)
        request = _load_request(job) if job is not None else {}
        report = _load_report(request)
        job_id = job.id if job is not None else ""
        active = bool(job is not None and job.status in {"pending", "running"})
    blocked_reason = ""
    if active:
        blocked_reason = "订单号补写任务正在排队或运行，请等待结果，不要重复提交。"
    else:
        if blocking_jobs(session_factory, ignore_job_id=job_id):
            blocked_reason = "有店铺页面任务正在排队或运行，请等待后再补写。"
    return {
        "person": ORDER_BACKFILL_PERSON,
        "lookback_hours": ORDER_BACKFILL_LOOKBACK_HOURS,
        "limit_default": ORDER_BACKFILL_LIMIT_DEFAULT,
        "available": not blocked_reason,
        "blocked_reason": blocked_reason,
        "latest_job": _job_payload(job),
        "report": report,
    }


def create_order_backfill(
    session_factory,
    *,
    store_id: str,
    limit: int | None,
    confirmation: str,
) -> dict[str, Any]:
    """创建补写任务：仅接受键入 y 的确认，不接收任意目标或规则覆盖。"""
    if confirmation.strip().lower() not in approval.CONFIRMATION_ANSWERS:
        raise approval.AutoApprovalServiceError(
            "confirmation-required", "补写订单号会写飞书，需要输入 y 明确确认。"
        )
    resolved_store_id = str(store_id or "").strip()
    if not resolved_store_id:
        raise approval.AutoApprovalServiceError(
            "store-required", "未指定店铺，无法读取待发货订单号。"
        )
    try:
        resolved_limit = max(0, int(limit if limit is not None else ORDER_BACKFILL_LIMIT_DEFAULT))
    except (TypeError, ValueError) as error:
        raise approval.AutoApprovalServiceError(
            "limit-invalid", "限量必须是 0 或正整数（0 = 不限量）。"
        ) from error
    if blocking_jobs(session_factory):
        raise approval.AutoApprovalServiceError(
            "store-busy", "有店铺页面任务正在排队或运行，请等待后再补写。", 409
        )
    result_path = approval.auto_approval_directory() / f"order_backfill_{uuid.uuid4()}.json"
    request = {
        "store_id": resolved_store_id,
        "result_path": str(result_path),
        "limit": resolved_limit,
        "write_feishu": True,
        "person": ORDER_BACKFILL_PERSON,
        "lookback_hours": ORDER_BACKFILL_LOOKBACK_HOURS,
    }
    job_id, deduplicated = approval._create_job(
        session_factory,
        job_type=ORDER_BACKFILL_JOB_TYPE,
        store_id=resolved_store_id,
        result_summary=json.dumps(request, ensure_ascii=False, sort_keys=True),
    )
    if deduplicated:
        raise approval.AutoApprovalServiceError(
            "store-busy", "已有订单号补写任务在排队或运行，请刷新任务状态。", 409
        )
    return {
        "job_id": job_id,
        "store_id": resolved_store_id,
        "limit": resolved_limit,
        "result_path": str(result_path),
    }
