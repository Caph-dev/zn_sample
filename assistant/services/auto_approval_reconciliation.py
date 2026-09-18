"""Historical reconciliation jobs, independent from the original approval state."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from assistant.database.models import AutoApprovalExecution, AutoApprovalPreview, Job
from assistant.services import auto_approval_service as approval
from assistant.services.page_lock import blocking_jobs

REPORT_FRESHNESS_SECONDS = 15 * 60
_creation_lock = threading.Lock()


def _backup_path(execution: AutoApprovalExecution) -> Path:
    prefix = Path(execution.backup_path)
    return prefix.with_name(prefix.name + "_pre_execute.json")


def _latest_job(session, execution_id: str) -> tuple[Job | None, dict]:
    return next(_reconciliation_jobs(session, execution_id), (None, {}))


def _reconciliation_jobs(session, execution_id: str):
    jobs = session.scalars(
        select(Job).where(Job.job_type == "auto_approval_reconcile")
        .order_by(Job.created_at.desc(), Job.id.desc())
    )
    for job in jobs:
        try:
            payload = json.loads(job.result_summary or "{}")
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("execution_id") == execution_id:
            yield job, payload


def _latest_evidence(session, execution: AutoApprovalExecution) -> tuple[dict | None, str]:
    for job, request in _reconciliation_jobs(session, execution.id):
        report = _load_report(request, execution)
        if report is not None:
            return report, ""
        if request.get("write_feishu"):
            return None, "上次补写结果文件缺失，无法排除已发生写入；请人工核对。"
    return None, ""


def _load_report(request: dict, execution: AutoApprovalExecution) -> dict | None:
    path = str(request.get("result_path") or "")
    if not path:
        return None
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(report, dict)
        or report.get("envelope_type") != "auto_approval_reconcile"
        or report.get("execution_id") != execution.id
        or report.get("store_id") != execution.store_id
        or not isinstance(report.get("items"), list)
        or not isinstance(report.get("rows"), list)
    ):
        return None
    try:
        expected_ids = set(json.loads(execution.apply_ids_json))
        for collection in (report["items"], report["rows"]):
            identifiers = [row["apply_id"] for row in collection]
            if len(identifiers) != len(expected_ids) or set(identifiers) != expected_ids:
                return None
    except (KeyError, TypeError, ValueError):
        return None
    return report


def _report_is_fresh(report: dict) -> bool:
    try:
        checked_at = datetime.fromisoformat(report["checked_at"])
        age = (datetime.now(timezone.utc) - checked_at).total_seconds()
        return 0 <= age <= REPORT_FRESHNESS_SECONDS
    except (KeyError, TypeError, ValueError):
        return False


def reconciliation_payload(session, execution: AutoApprovalExecution) -> dict[str, Any]:
    latest_job, request = _latest_job(session, execution.id)
    report = _load_report(request, execution)
    approval_job = session.get(Job, execution.job_id)
    approval_active = bool(
        approval_job and approval_job.job_type == "auto_approval_execute"
        and approval_job.status in {"pending", "running"}
    )
    state_error = ""
    if execution.status == "queued" and not approval_active:
        state_error = "历史批次状态异常：批次标为 queued，但没有对应的排队/运行中批准任务。请核验原批准结果后恢复本地状态，不要等待或重新批准。"
    blocked_reason = ""
    if state_error:
        blocked_reason = state_error
    elif approval_active:
        blocked_reason = "原批准任务正在排队或运行，请等待结果。"
    elif execution.status not in {"completed", "needs_reconcile"}:
        blocked_reason = "批准批次尚未完成或执行失败；不能将未批准的申请当成补写目标。"
    elif not execution.backup_path or not _backup_path(execution).is_file():
        blocked_reason = "执行备份缺失，请人工核对；不能自动补写。"
    elif latest_job and latest_job.status in {"pending", "running"}:
        blocked_reason = "本批次核对任务正在排队或运行，请等待结果。"
    repair_blocked_reason = blocked_reason
    if not repair_blocked_reason:
        _, evidence_error = _latest_evidence(session, execution)
        if evidence_error:
            repair_blocked_reason = evidence_error
        elif not latest_job or latest_job.status != "succeeded" or not report:
            repair_blocked_reason = "请先完成一次只读核对；任务失败或结果未知不能直接重试写入。"
        elif not _report_is_fresh(report):
            repair_blocked_reason = "核对结果已超过 15 分钟，请重新只读核对。"
        elif not any(item.get("can_repair") is True for item in report["items"]):
            repair_blocked_reason = "没有已确认可安全补写的缺失项；查看逐行核对说明。"
    return {
        "approval_active": approval_active, "state_error": state_error,
        "available": not blocked_reason, "blocked_reason": blocked_reason,
        "can_repair": not repair_blocked_reason,
        "repair_blocked_reason": repair_blocked_reason,
        "report": {key: value for key, value in report.items() if key != "rows"} if report else None,
        "latest_job": {
            "job_id": latest_job.id, "status": latest_job.status,
            "progress_current": latest_job.progress_current,
            "progress_total": latest_job.progress_total,
            "progress_message": latest_job.progress_message,
            "error_code": latest_job.error_code,
            "error_summary": latest_job.error_summary, "log_path": latest_job.log_path,
        } if latest_job else None,
    }


def create_reconciliation(
    session_factory, *, execution_id: str, write_feishu: bool, confirmation: str,
) -> dict[str, str]:
    if write_feishu and confirmation.strip().lower() not in approval.CONFIRMATION_ANSWERS:
        raise approval.AutoApprovalServiceError("confirmation-required", "补写缺失项会写飞书，需要输入 y 明确确认。")
    with _creation_lock, session_factory() as session:
        execution = session.get(AutoApprovalExecution, execution_id)
        if execution is None:
            raise approval.AutoApprovalServiceError("execution-not-found", "执行批次不存在", 404)
        state = reconciliation_payload(session, execution)
        if not state["available"]:
            raise approval.AutoApprovalServiceError("reconciliation-unavailable", state["blocked_reason"], 409)
        if write_feishu and not state["can_repair"]:
            raise approval.AutoApprovalServiceError("verification-required", state["repair_blocked_reason"], 409)
        if blocking_jobs(session_factory):
            raise approval.AutoApprovalServiceError("store-busy", "有店铺页面任务正在排队或运行，请等待后再核对。", 409)
        preview = session.get(AutoApprovalPreview, execution.preview_id)
        if preview is None:
            raise approval.AutoApprovalServiceError("preview-not-found", "原批次规则快照不存在", 409)
        try:
            backup = json.loads(_backup_path(execution).read_text(encoding="utf-8"))
            if isinstance(backup, dict):
                backup = backup.get("rows") or backup.get("items")
            if not isinstance(backup, list):
                raise ValueError("备份不是行数组")
            selected_ids = set(json.loads(execution.apply_ids_json))
            rows = [dict(row) for row in backup if isinstance(row, dict) and row.get("apply_id") in selected_ids]
            if len(rows) != len(selected_ids) or {row["apply_id"] for row in rows} != selected_ids:
                raise ValueError("备份缺少本批次所选申请或包含重复申请")
        except (OSError, ValueError, TypeError) as error:
            raise approval.AutoApprovalServiceError("backup-invalid", f"执行备份无效：{error}", 409) from error
        # Carry forward every saved write outcome, even from an interrupted job.
        # Never fall back to the original 'missing' state after an uncertain write.
        previous_report, evidence_error = _latest_evidence(session, execution)
        if evidence_error:
            # Missing write output must not prevent a safe read-back, but every
            # row remains uncertain until actual remote evidence resolves it.
            for row in rows:
                row["feishu_relation_status"] = "write-uncertain"
                row["order_backfill_status"] = "write-uncertain"
        if previous_report:
            previous_rows = {row["apply_id"]: row for row in previous_report["rows"]}
            for row in rows:
                previous = previous_rows.get(row["apply_id"])
                if previous:
                    if any(previous.get(key) != row.get(key) for key in ("creator_id", "creator_name", "product_id")):
                        raise approval.AutoApprovalServiceError("reconciliation-identity-mismatch", "核对记录与原执行身份不一致", 409)
                    row.update(previous)
        repair_ids = [item["apply_id"] for item in (state["report"] or {}).get("items", []) if item.get("can_repair") is True] if write_feishu else []
        run_id = str(uuid.uuid4())
        snapshot_path = approval.rule_snapshot_directory() / f"reconcile_{run_id}.json"
        result_path = approval.auto_approval_directory() / f"reconcile_{execution_id}_{run_id}.json"
        snapshot_path.write_text(json.dumps({
            "envelope_type": "auto_approval_reconcile_input",
            "execution_id": execution.id, "store_id": execution.store_id,
            "rule_hash": execution.rule_hash, "rows": rows,
            "repair_apply_ids": repair_ids, "limit": execution.limit_count,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        request = {
            "execution_id": execution.id, "preview_id": execution.preview_id,
            "rules_path": preview.rules_path, "preview_path": preview.result_path,
            "backup_path": str(snapshot_path), "result_path": str(result_path),
            "limit": execution.limit_count, "write_feishu": write_feishu,
        }
        job_id, deduplicated = approval._create_job(
            session_factory, job_type="auto_approval_reconcile", store_id=execution.store_id,
            result_summary=json.dumps(request, ensure_ascii=False, sort_keys=True),
        )
        if deduplicated:
            raise approval.AutoApprovalServiceError("store-busy", "已有核对任务，请刷新任务状态。", 409)
        # Keep original status/job_id/results intact; reconciliation has its own job.
        return {"execution_id": execution_id, "job_id": job_id}
