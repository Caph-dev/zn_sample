"""Evidence-based repair of legacy reconciliation's local batch association.

No platform or Feishu calls. Read-only planning is separate from the explicitly
requested database update; never infer approval success from reconciliation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from assistant.database.engine import backup_sqlite
from assistant.database.models import AutoApprovalExecution, AutoApprovalExecutionItem, Job
from assistant.services.auto_approval_service import AutoApprovalServiceError

ACTIVE_STATUSES = {"pending", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


def _payload(job: Job) -> dict[str, Any]:
    try:
        payload = json.loads(job.result_summary or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _reject(message: str) -> None:
    raise AutoApprovalServiceError("legacy-recovery-blocked", message, 409)


def plan_legacy_execution_recovery(session, execution_id: str) -> dict[str, Any]:
    execution = session.get(AutoApprovalExecution, execution_id)
    if execution is None:
        _reject("执行批次不存在")
    linked_job = session.get(Job, execution.job_id)
    if (
        linked_job and linked_job.job_type == "auto_approval_execute"
        and execution.status in {"completed", "needs_reconcile"}
        and linked_job.status == "succeeded"
        and _payload(linked_job).get("execution_id") == execution.id
    ):
        return {"execution_id": execution_id, "changed": False, "status": execution.status, "job_id": execution.job_id}
    if execution.status != "queued" or not linked_job or linked_job.job_type != "auto_approval_reconcile":
        _reject("不是旧核对覆盖 queued/job_id 的已知异常，不自动修复")
    if (
        linked_job.status not in TERMINAL_STATUSES
        or linked_job.store_id != execution.store_id
        or _payload(linked_job).get("execution_id") != execution.id
    ):
        _reject("关联核对任务尚未结束或批次/店铺不一致")
    if session.scalar(select(Job.id).where(Job.status.in_(ACTIVE_STATUSES)).limit(1)):
        _reject("有任务正在排队或运行，暂不恢复历史状态")
    original_jobs = [
        job for job in session.scalars(select(Job).where(Job.job_type == "auto_approval_execute"))
        if _payload(job).get("execution_id") == execution.id
    ]
    if len(original_jobs) != 1:
        _reject("不能唯一定位原批准任务")
    original_job = original_jobs[0]
    original_payload = _payload(original_job)
    if original_job.status != "succeeded" or original_job.store_id != execution.store_id or not original_job.finished_at:
        _reject("原批准任务没有可靠的成功终态，不从核对成功推断批准成功")
    if original_payload.get("preview_id") not in {None, "", execution.preview_id}:
        _reject("原批准任务预览关联不一致")
    if original_payload.get("result_path") and original_payload["result_path"] != execution.result_path:
        _reject("原批准任务结果路径不一致")
    try:
        result = json.loads(Path(execution.result_path).read_text(encoding="utf-8"))
        selected_ids = json.loads(execution.apply_ids_json)
        if not isinstance(result, dict) or not isinstance(selected_ids, list):
            raise ValueError("结果或所选申请格式不正确")
        if (
            result.get("envelope_type") != "auto_approval_execution"
            or result.get("schema_version") != 1
            or result.get("execution_id") != execution.id
            or result.get("store_id") != execution.store_id
            or result.get("rule_hash") != execution.rule_hash
            or result.get("limit") != execution.limit_count
            or result.get("preview_id") not in {None, "", execution.preview_id}
            or not result.get("finished_at")
        ):
            raise ValueError("执行信封与数据库批次不一致")
        result_rows = result.get("items")
        if not isinstance(result_rows, list) or not result_rows:
            raise ValueError("没有原批准明细")
        result_ids = [row["apply_id"] for row in result_rows]
        if len(set(selected_ids)) != len(selected_ids) or set(result_ids) != set(selected_ids) or len(result_ids) != len(selected_ids):
            raise ValueError("原执行结果缺少所选行或含重复行")
        stored_items = session.scalars(select(AutoApprovalExecutionItem).where(
            AutoApprovalExecutionItem.execution_id == execution.id,
        )).all()
        stored_by_apply = {item.apply_id: item for item in stored_items}
        if len(stored_items) != len(selected_ids) or set(stored_by_apply) != set(selected_ids):
            raise ValueError("本地批准明细不完整")
        for row in result_rows:
            item = stored_by_apply[row["apply_id"]]
            if row.get("approve_status") not in {"approved", "unknown", "skipped", "failed", "error"}:
                raise ValueError("原执行明细不是终态")
            for field in ("creator_id", "creator_name", "product_id", "approve_status"):
                if not row.get(field) or row[field] != getattr(item, field):
                    raise ValueError(f"执行明细 {field} 与数据库不一致")
    except (OSError, ValueError, KeyError, TypeError) as error:
        _reject(f"原批准结果证据不足：{error}")
    recovered_status = "needs_reconcile" if any(row["approve_status"] == "unknown" for row in result_rows) else "completed"
    return {
        "execution_id": execution.id, "changed": True,
        "before": {"status": execution.status, "job_id": execution.job_id},
        "after": {"status": recovered_status, "job_id": original_job.id},
        "finished_at": original_job.finished_at.isoformat(),
        "result_path": execution.result_path,
    }


def recover_legacy_execution(session_factory, execution_id: str, *, backup_directory: Path) -> dict[str, Any]:
    """Explicit local maintenance with full SQLite backup and pre-update audit."""
    with session_factory() as session:
        plan = plan_legacy_execution_recovery(session, execution_id)
    if not plan["changed"]:
        return plan
    backup_directory.mkdir(parents=True, exist_ok=False)
    backup_path = backup_directory / "assistant.sqlite3"
    backup_sqlite(session_factory.kw["bind"], backup_path)
    with session_factory() as session:
        # Serialize against queue claiming, then repeat every evidence check.
        session.execute(text("BEGIN IMMEDIATE"))
        plan = plan_legacy_execution_recovery(session, execution_id)
        (backup_directory / "recovery.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        if plan["changed"]:
            execution = session.get(AutoApprovalExecution, execution_id)
            original_job = session.get(Job, plan["after"]["job_id"])
            execution.status = plan["after"]["status"]
            execution.job_id = original_job.id
            execution.finished_at = original_job.finished_at
            # Do not rewrite row outcomes, Feishu state, or any historical job.
            session.commit()
    return {**plan, "database_backup": str(backup_path)}
