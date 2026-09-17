"""自动审批：预览/执行/核对的领域服务与持久化协调。

职责边界：
- 规则校验与快照只在服务端完成；页面提交的结构化规则先在这里过严格 schema。
- 预览与执行都通过既有持久任务队列执行固定 argv 的子进程脚本。
- 执行批准只接受「服务器生成的预览 + 规则 hash 一致 + 新鲜度未过期」的证据。
- hash 只用于一致性校验，不作为授权依据。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from assistant.database.models import (
    AutoApprovalCandidate,
    AutoApprovalExecution,
    AutoApprovalExecutionItem,
    AutoApprovalPreview,
)
from assistant.paths import ensure_user_dirs

# 服务端固定边界（与 lib.auto_approval_rules 一致；重复声明以便离线校验）。
PREVIEW_FRESHNESS_SECONDS = 24 * 60 * 60
EXECUTE_LIMIT_DEFAULT = 1
IDEMPOTENCY_KEY_MAX_LENGTH = 128
CONFIRMATION_ANSWERS = frozenset({"y", "yes"})

# 自定义规则草稿的记忆键（AppSetting 非敏感键；仅用于页面回填，不作为执行授权）。
RULE_DRAFT_SETTING_KEY = "auto_approval_rule_draft"

# 主推款选项缓存：飞书网络读取有成本，短 TTL 缓存。
_hero_cache_lock = threading.Lock()
_hero_cache: dict[str, Any] = {"fetched_at": 0.0, "data": None, "error": None}
HERO_CACHE_TTL_SECONDS = 300.0
HERO_ERROR_CACHE_TTL_SECONDS = 30.0


class AutoApprovalServiceError(RuntimeError):
    """带稳定错误码与 HTTP 状态的服务层错误。"""

    def __init__(self, code: str, summary: str, status_code: int = 400) -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary
        self.status_code = status_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def auto_approval_directory() -> Path:
    application_directory = ensure_user_dirs()
    directory = application_directory / "exports" / "auto_approval"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def rule_snapshot_directory() -> Path:
    application_directory = ensure_user_dirs()
    directory = application_directory / "config" / "auto_approval"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_hero_products(*, force: bool = False) -> dict[str, Any]:
    """读取主推款商品选项（带进程内短缓存）。"""
    with _hero_cache_lock:
        now = time.monotonic()
        ttl = HERO_ERROR_CACHE_TTL_SECONDS if _hero_cache.get("error") else HERO_CACHE_TTL_SECONDS
        if (
            not force
            and _hero_cache.get("data") is not None
            and now - _hero_cache.get("fetched_at", 0.0) < ttl
        ):
            return dict(_hero_cache["data"])
    try:
        from lib.feishu_bitable import build_product_id_to_sku_map
        from lib.feishu_hero import load_hero_from_feishu

        hero_data = load_hero_from_feishu(
            url=None,
            sheet_title=None,
            app_id=None,
            app_secret=None,
            config_path=None,
        )
    except Exception as error:
        with _hero_cache_lock:
            _hero_cache.update(
                {
                    "fetched_at": time.monotonic(),
                    "data": None,
                    "error": str(error),
                }
            )
        return {"ok": False, "error": "hero-unavailable", "detail": str(error)}

    product_id_to_sku = build_product_id_to_sku_map(hero_data)
    products = sorted(
        (
            {
                "product_id": product_id,
                "sku": product_id_to_sku.get(product_id, ""),
            }
            for product_id in set(product_id_to_sku)
        ),
        key=lambda entry: (entry["sku"] == "", entry["sku"]),
    )
    payload = {
        "ok": True,
        "products": products,
        "hero_keys": sorted(hero_data.get("hero_keys") or set()),
        "doc_title": hero_data.get("doc_title") or "",
        "sheet_title": hero_data.get("sheet_title") or "",
    }
    with _hero_cache_lock:
        _hero_cache.update(
            {"fetched_at": time.monotonic(), "data": payload, "error": None}
        )
    return payload


def options_payload(session_factory=None) -> dict[str, Any]:
    """GET /api/auto-approval/options 的载荷：条件范围、默认值、主推商品与上次草稿。"""
    from lib.auto_approval_rules import (
        ALLOWED_CATEGORIES,
        BASIC_LIMITS,
        CONTENT_ALLOWED_DAYS,
        CONTENT_ALLOWED_MIN_RELATED,
        EXECUTE_LIMIT_DEFAULT,
        PREVIEW_FRESHNESS_SECONDS,
        VIDEO_LIVE_LIMITS,
    )

    hero = load_hero_products()
    saved_rule = (
        load_rule_draft(session_factory, hero=hero)
        if session_factory is not None
        else None
    )
    return {
        "schema_version": 1,
        "mode": "custom",
        "basic": BASIC_LIMITS,
        "categories": sorted(ALLOWED_CATEGORIES),
        "video_live": VIDEO_LIVE_LIMITS,
        "content": {
            "days": list(CONTENT_ALLOWED_DAYS),
            "min_related": list(CONTENT_ALLOWED_MIN_RELATED),
        },
        "limits": {
            "execute_limit_default": EXECUTE_LIMIT_DEFAULT,
            "preview_freshness_seconds": PREVIEW_FRESHNESS_SECONDS,
        },
        "hero": hero,
        "saved_rule": saved_rule,
    }


def load_rule_draft(
    session_factory,
    *,
    hero: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """读取上次保存的自定义规则草稿；不合法（含商品已不在主推）则忽略。"""
    from assistant.database.models import AppSetting
    from lib.auto_approval_rules import (
        AutoApprovalRuleError,
        validate_custom_rule,
    )

    if session_factory is None:
        return None
    with session_factory() as session:
        setting = session.get(AppSetting, RULE_DRAFT_SETTING_KEY)
        if setting is None:
            return None
        raw_value = str(setting.value or "")
    try:
        stored = json.loads(raw_value)
    except ValueError:
        return None
    if not isinstance(stored, dict):
        return None
    rule_payload = stored.get("rule")
    if not isinstance(rule_payload, dict):
        return None
    hero = hero if hero is not None else load_hero_products()
    if not hero.get("ok"):
        return None
    allowed_product_ids = {entry["product_id"] for entry in hero["products"]}
    try:
        rule = validate_custom_rule(
            rule_payload,
            allowed_product_ids=allowed_product_ids,
        )
    except AutoApprovalRuleError:
        return None
    return {
        "rule": rule.to_dict(),
        "saved_at": str(stored.get("saved_at") or ""),
    }


def save_rule_draft(session_factory, rule_payload: dict[str, Any]) -> dict[str, Any]:
    """保存自定义规则草稿（严格校验后才写入；草稿不作为执行授权）。"""
    from assistant.database.models import AppSetting
    from lib.auto_approval_rules import (
        AutoApprovalRuleError,
        validate_custom_rule,
    )

    hero = load_hero_products()
    if not hero.get("ok"):
        raise AutoApprovalServiceError(
            "hero-unavailable",
            "主推款表不可用，无法保存自定义规则。",
            status_code=503,
        )
    allowed_product_ids = {entry["product_id"] for entry in hero["products"]}
    try:
        rule = validate_custom_rule(
            rule_payload,
            allowed_product_ids=allowed_product_ids,
        )
    except AutoApprovalRuleError as error:
        raise AutoApprovalServiceError(error.code, error.summary, status_code=400) from error
    saved_at = _utc_now().isoformat(timespec="seconds")
    with session_factory() as session:
        setting = session.get(AppSetting, RULE_DRAFT_SETTING_KEY)
        value = json.dumps(
            {"rule": rule.to_dict(), "saved_at": saved_at},
            ensure_ascii=False,
            sort_keys=True,
        )
        if setting is None:
            session.add(AppSetting(key=RULE_DRAFT_SETTING_KEY, value=value))
        else:
            setting.value = value
        session.commit()
    return {"rule": rule.to_dict(), "saved_at": saved_at}


def create_preview(
    session_factory,
    *,
    rule_payload: dict[str, Any],
    store_id: str,
) -> dict[str, str]:
    """校验规则并创建只读筛查任务（任务队列串行执行）。"""
    from lib.auto_approval_rules import (
        AutoApprovalRuleError,
        rule_hash,
        rule_summary_lines,
        validate_custom_rule,
    )

    hero = load_hero_products()
    if not hero.get("ok"):
        raise AutoApprovalServiceError(
            "hero-unavailable",
            "主推款表不可用，无法创建自定义规则。请先检查飞书配置。",
            status_code=503,
        )
    allowed_product_ids = {entry["product_id"] for entry in hero["products"]}
    try:
        rule = validate_custom_rule(
            rule_payload,
            allowed_product_ids=allowed_product_ids,
        )
    except AutoApprovalRuleError as error:
        raise AutoApprovalServiceError(
            error.code,
            error.summary,
            status_code=400,
        ) from error

    product_display = {
        entry["product_id"]: entry["sku"] for entry in hero["products"]
    }
    summary_lines = rule_summary_lines(rule, product_display=product_display)
    # 记住本次规则，下次进入页面可回填（草稿仅用于回填，不构成执行授权）。
    save_rule_draft(session_factory, rule.to_dict())
    preview_id = str(uuid.uuid4())
    rules_path = rule_snapshot_directory() / f"rules_{preview_id}.json"
    rules_path.write_text(
        json.dumps(rule.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result_path = auto_approval_directory() / f"preview_{preview_id}.json"

    job_id, _deduplicated = _create_job(
        session_factory,
        job_type="auto_approval_preview",
        store_id=store_id,
        result_summary=json.dumps(
            {
                "preview_id": preview_id,
                "rules_path": str(rules_path),
                "result_path": str(result_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    with session_factory() as session:
        session.add(
            AutoApprovalPreview(
                id=preview_id,
                store_id=store_id,
                job_id=job_id,
                rule_json=json.dumps(rule.to_dict(), ensure_ascii=False, sort_keys=True),
                rule_hash=rule_hash(rule),
                rule_summary=json.dumps(summary_lines, ensure_ascii=False),
                rules_path=str(rules_path),
                result_path=str(result_path),
                status="queued",
            )
        )
        session.commit()
    return {"preview_id": preview_id, "job_id": job_id}


def _create_job(
    session_factory,
    *,
    job_type: str,
    store_id: str,
    result_summary: str,
) -> tuple[str, bool]:
    """创建任务行；与既有 Ziniao 互斥的并发防重由队列与锁层负责。"""
    from assistant.jobs.locks import create_or_get_pending_job

    return create_or_get_pending_job(
        session_factory,
        job_type=job_type,
        store_id=store_id,
        result_summary=result_summary,
    )


def preview_payload(session_factory, preview_id: str, *, include_rows: bool = True) -> dict[str, Any]:
    with session_factory() as session:
        preview = session.get(AutoApprovalPreview, preview_id)
        if preview is None:
            raise AutoApprovalServiceError(
                "preview-not-found", "预览不存在", status_code=404
            )
        payload: dict[str, Any] = {
            "preview_id": preview.id,
            "store_id": preview.store_id,
            "status": preview.status,
            "job_id": preview.job_id,
            "rule_hash": preview.rule_hash,
            "rule": json.loads(preview.rule_json or "{}"),
            "rule_summary": json.loads(preview.rule_summary or "[]"),
            "integrity_complete": preview.integrity_complete,
            "integrity_notes": json.loads(preview.integrity_notes or "[]"),
            "stats": json.loads(preview.stats or "{}"),
            "error_code": preview.error_code,
            "error_summary": preview.error_summary,
            "created_at": preview.created_at.isoformat(),
            "finished_at": preview.finished_at.isoformat() if preview.finished_at else None,
            "freshness_seconds": PREVIEW_FRESHNESS_SECONDS,
            "is_fresh": _preview_is_fresh(preview),
        }
        if not include_rows:
            return payload
        candidates = session.scalars(
            select(AutoApprovalCandidate)
            .where(AutoApprovalCandidate.preview_id == preview_id)
            .order_by(AutoApprovalCandidate.id)
        ).all()
        payload["rows"] = [candidate_payload(candidate) for candidate in candidates]
        return payload


def candidate_payload(candidate: AutoApprovalCandidate) -> dict[str, Any]:
    return {
        "apply_id": candidate.apply_id,
        "creator_id": candidate.creator_id,
        "creator_name": candidate.creator_name,
        "product_id": candidate.product_id,
        "overall": candidate.overall,
        "content_verdict": candidate.content_verdict,
        "custom_eligible": candidate.custom_eligible,
        "blocked": candidate.blocked,
        "checks": json.loads(candidate.checks_json or "[]"),
        "safety_blocks": json.loads(candidate.safety_blocks_json or "[]"),
        "metrics": json.loads(candidate.metrics_json or "{}"),
        "content_status": candidate.content_status,
        "content_reason": candidate.content_reason,
        "content_evidence_path": candidate.content_evidence_path,
        "content_related_count": candidate.content_related_count,
        "content_complete": candidate.content_complete,
        "observed_at": candidate.observed_at,
    }


def _preview_is_fresh(preview: AutoApprovalPreview) -> bool:
    if preview.finished_at is None:
        return False
    finished_at = preview.finished_at
    if finished_at.tzinfo is None:
        # SQLite 会剥掉 DateTime 时区（见 assistant/database/types.py），按 UTC 处理。
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    elapsed = (_utc_now() - finished_at).total_seconds()
    return 0 <= elapsed <= PREVIEW_FRESHNESS_SECONDS


def create_execution(
    session_factory,
    *,
    preview_id: str,
    apply_ids: list[str],
    limit: int,
    write_feishu: bool,
    confirmation: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """确认后创建限量批准任务；只接受服务器生成的预览证据。"""
    normalized_confirmation = str(confirmation or "").strip().lower()
    if normalized_confirmation not in CONFIRMATION_ANSWERS:
        raise AutoApprovalServiceError(
            "confirmation-required",
            "执行批准需要输入 y 明确确认。",
            status_code=400,
        )
    normalized_key = str(idempotency_key or "").strip()
    if not normalized_key or len(normalized_key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise AutoApprovalServiceError(
            "invalid-idempotency-key",
            f"幂等键必须非空且不超过 {IDEMPOTENCY_KEY_MAX_LENGTH} 字符。",
            status_code=400,
        )
    if not isinstance(apply_ids, list) or not apply_ids:
        raise AutoApprovalServiceError(
            "missing-apply-ids", "至少选择一行候选。", status_code=400
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise AutoApprovalServiceError(
            "invalid-limit",
            f"限量必须为正整数（默认 {EXECUTE_LIMIT_DEFAULT}，无上限）。",
            status_code=400,
        )
    if len(apply_ids) > limit:
        raise AutoApprovalServiceError(
            "selection-over-limit",
            f"所选 {len(apply_ids)} 行超过限量 {limit}。",
            status_code=400,
        )
    normalized_ids: list[str] = []
    for apply_id in apply_ids:
        value = str(apply_id).strip()
        if not value:
            raise AutoApprovalServiceError(
                "invalid-apply-id", "申请 ID 必须非空。", status_code=400
            )
        if value in normalized_ids:
            raise AutoApprovalServiceError(
                "duplicate-apply-id", f"申请 ID 重复: {value}", status_code=400
            )
        normalized_ids.append(value)

    with session_factory() as session:
        existing = session.scalar(
            select(AutoApprovalExecution).where(
                AutoApprovalExecution.idempotency_key == normalized_key
            )
        )
        if existing is not None:
            return {
                "execution_id": existing.id,
                "job_id": existing.job_id,
                "deduplicated": True,
                "status": existing.status,
            }
        preview = session.get(AutoApprovalPreview, preview_id)
        if preview is None:
            raise AutoApprovalServiceError(
                "preview-not-found", "预览不存在", status_code=404
            )
        if preview.status != "completed":
            raise AutoApprovalServiceError(
                "preview-not-ready",
                f"预览状态为 {preview.status}，不可执行；请等待筛查完成。",
                status_code=409,
            )
        if not preview.integrity_complete:
            raise AutoApprovalServiceError(
                "preview-incomplete",
                "预览采集不完整，禁止执行；请重新筛查。",
                status_code=409,
            )
        if not _preview_is_fresh(preview):
            raise AutoApprovalServiceError(
                "preview-stale",
                f"预览已超过 {PREVIEW_FRESHNESS_SECONDS // 3600} 小时，"
                "禁止执行；请重新筛查。",
                status_code=409,
            )
        eligible_candidates = {
            candidate.apply_id: candidate
            for candidate in session.scalars(
                select(AutoApprovalCandidate).where(
                    AutoApprovalCandidate.preview_id == preview_id,
                    AutoApprovalCandidate.custom_eligible.is_(True),
                )
            ).all()
        }
        disallowed = [
            apply_id for apply_id in normalized_ids if apply_id not in eligible_candidates
        ]
        if disallowed:
            raise AutoApprovalServiceError(
                "candidate-not-eligible",
                f"所选行不在可批准名单中: {disallowed}",
                status_code=400,
            )
        from lib.auto_approval_sku import has_product_sku_evidence

        for apply_id in normalized_ids:
            candidate = eligible_candidates[apply_id]
            metrics = json.loads(candidate.metrics_json or "{}")
            if not has_product_sku_evidence(
                {"product_id": candidate.product_id, "sku_desc": metrics.get("sku_desc")},
                json.loads(candidate.checks_json or "[]"),
            ):
                raise AutoApprovalServiceError(
                    "b005-sku-evidence-invalid",
                    f"申请 {apply_id} 缺少有效的 B005 SKU 检查证据，或 SKU 含 6PCS；请重新筛查。",
                    status_code=409,
                )
        execution_id = str(uuid.uuid4())
        apply_ids_path = rule_snapshot_directory() / f"apply_ids_{execution_id}.json"
        apply_ids_path.write_text(
            json.dumps(normalized_ids, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result_path = auto_approval_directory() / f"execution_{execution_id}.json"
        backup_prefix = auto_approval_directory() / f"execution_{execution_id}"
        session.add(
            AutoApprovalExecution(
                id=execution_id,
                preview_id=preview_id,
                idempotency_key=normalized_key,
                store_id=preview.store_id,
                rule_hash=preview.rule_hash,
                apply_ids_json=json.dumps(normalized_ids, ensure_ascii=False),
                limit_count=limit,
                write_feishu=bool(write_feishu),
                confirmation_recorded=True,
                status="queued",
                result_path=str(result_path),
                backup_path=str(backup_prefix),
            )
        )
        for apply_id in normalized_ids:
            candidate = eligible_candidates[apply_id]
            session.add(
                AutoApprovalExecutionItem(
                    execution_id=execution_id,
                    apply_id=apply_id,
                    creator_id=candidate.creator_id,
                    creator_name=candidate.creator_name,
                    product_id=candidate.product_id,
                    approve_status="queued",
                )
            )
        session.commit()
        job_id, _deduplicated = _create_job(
            session_factory,
            job_type="auto_approval_execute",
            store_id=preview.store_id,
            result_summary=json.dumps(
                {
                    "execution_id": execution_id,
                    "preview_id": preview_id,
                    "rules_path": preview.rules_path,
                    "result_path": str(result_path),
                    "preview_path": preview.result_path,
                    "apply_ids_path": str(apply_ids_path),
                    "backup_prefix": str(backup_prefix),
                    "limit": limit,
                    "write_feishu": bool(write_feishu),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        execution = session.get(AutoApprovalExecution, execution_id)
        execution.job_id = job_id
        session.commit()
    return {
        "execution_id": execution_id,
        "job_id": job_id,
        "deduplicated": False,
        "status": "queued",
    }


def execution_payload(session_factory, execution_id: str) -> dict[str, Any]:
    with session_factory() as session:
        execution = session.get(AutoApprovalExecution, execution_id)
        if execution is None:
            raise AutoApprovalServiceError(
                "execution-not-found", "执行批次不存在", status_code=404
            )
        items = session.scalars(
            select(AutoApprovalExecutionItem)
            .where(AutoApprovalExecutionItem.execution_id == execution_id)
            .order_by(AutoApprovalExecutionItem.id)
        ).all()
        return {
            "execution_id": execution.id,
            "preview_id": execution.preview_id,
            "job_id": execution.job_id,
            "status": execution.status,
            "store_id": execution.store_id,
            "rule_hash": execution.rule_hash,
            "apply_ids": json.loads(execution.apply_ids_json or "[]"),
            "limit": execution.limit_count,
            "write_feishu": execution.write_feishu,
            "error_code": execution.error_code,
            "error_summary": execution.error_summary,
            "created_at": execution.created_at.isoformat(),
            "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
            "items": [
                {
                    "apply_id": item.apply_id,
                    "creator_id": item.creator_id,
                    "creator_name": item.creator_name,
                    "product_id": item.product_id,
                    "approve_status": item.approve_status,
                    "approve_error": item.approve_error,
                    "action": item.action,
                    "platform_confirmation_status": item.platform_confirmation_status,
                    "feishu_relation_status": item.feishu_relation_status,
                    "feishu_record_id": item.feishu_record_id,
                    "feishu_error": item.feishu_error,
                    "approved_at": item.approved_at,
                }
                for item in items
            ],
        }


def create_reconcile(
    session_factory,
    *,
    execution_id: str,
    write_feishu: bool,
    confirmation: str,
) -> dict[str, str]:
    """对已完成执行批次创建核对补写任务（不重新批准）。"""
    normalized_confirmation = str(confirmation or "").strip().lower()
    if normalized_confirmation not in CONFIRMATION_ANSWERS:
        raise AutoApprovalServiceError(
            "confirmation-required",
            "核对补写会写飞书（补记录/回填订单号），需要输入 y 明确确认。",
            status_code=400,
        )
    with session_factory() as session:
        execution = session.get(AutoApprovalExecution, execution_id)
        if execution is None:
            raise AutoApprovalServiceError(
                "execution-not-found", "执行批次不存在", status_code=404
            )
        if execution.status not in {"completed", "needs_reconcile"}:
            raise AutoApprovalServiceError(
                "execution-not-ready",
                f"执行批次状态为 {execution.status}，不可核对；请等待执行完成。",
                status_code=409,
            )
        preview = session.get(AutoApprovalPreview, execution.preview_id)
        if preview is None:
            raise AutoApprovalServiceError(
                "preview-not-found", "预览不存在", status_code=404
            )
        backup_rows_path = Path(execution.backup_path).with_name(
            Path(execution.backup_path).name + "_pre_execute.json"
        )
        if not backup_rows_path.is_file():
            raise AutoApprovalServiceError(
                "backup-missing",
                "执行备份缺失，无法核对；请人工核对平台与飞书状态。",
                status_code=409,
            )
        reconcile_out = auto_approval_directory() / f"reconcile_{execution_id}.json"
        job_id, _deduplicated = _create_job(
            session_factory,
            job_type="auto_approval_reconcile",
            store_id=execution.store_id,
            result_summary=json.dumps(
                {
                    "execution_id": execution_id,
                    "preview_id": execution.preview_id,
                    "rules_path": preview.rules_path,
                    "preview_path": preview.result_path,
                    "backup_path": str(backup_rows_path),
                    "result_path": str(reconcile_out),
                    "write_feishu": bool(write_feishu),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        execution.status = "queued"
        execution.job_id = job_id
        session.commit()
    return {"execution_id": execution_id, "job_id": job_id}


def mark_job_cancelled(session_factory, job_id: str) -> None:
    """任务被取消时同步预览/执行批次，避免一直停在 queued。"""
    with session_factory() as session:
        preview = session.scalar(
            select(AutoApprovalPreview).where(AutoApprovalPreview.job_id == job_id)
        )
        preview_id = preview.id if preview is not None else ""
        execution = session.scalar(
            select(AutoApprovalExecution).where(AutoApprovalExecution.job_id == job_id)
        )
        execution_id = execution.id if execution is not None else ""
    if preview_id:
        sync_preview_result(
            session_factory,
            preview_id,
            status="cancelled",
            error_code="cancelled",
            error_summary="只读筛查已取消。",
        )
    if execution_id:
        sync_execution_result(
            session_factory,
            execution_id,
            status="cancelled",
            error_code="cancelled",
            error_summary="执行任务已取消。",
        )


def sync_preview_result(session_factory, preview_id: str, *, status: str, error_code: str = "", error_summary: str = "") -> None:
    """任务完成后同步预览行状态（子进程 handler 调用）。"""
    with session_factory() as session:
        preview = session.get(AutoApprovalPreview, preview_id)
        if preview is None:
            return
        preview.status = status
        preview.error_code = error_code
        preview.error_summary = error_summary
        preview.finished_at = _utc_now()
        if status == "completed":
            try:
                result = json.loads(Path(preview.result_path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                session.commit()
                return
            preview.integrity_complete = bool(result.get("integrity_complete"))
            preview.integrity_notes = json.dumps(
                result.get("integrity_notes") or [], ensure_ascii=False
            )
            preview.stats = json.dumps(result.get("stats") or {}, ensure_ascii=False)
            existing = {
                candidate.apply_id: candidate
                for candidate in session.scalars(
                    select(AutoApprovalCandidate).where(
                        AutoApprovalCandidate.preview_id == preview_id
                    )
                ).all()
            }
            metric_keys = (
                "sku_id",
                "sku_desc",
                "followers_n",
                "gmv_n",
                "units_n",
                "gpm_proxy_n",
                "overall_gpm_n",
                "aov_n",
                "aov_detail_n",
                "fulfillment_n",
                "est_post_rate_n",
                "female_pct",
                "categories_n",
                "video_gpm_n",
                "live_gpm_n",
                "avg_video_views_n",
                "avg_live_views_n",
                "video_engagement_n",
                "detail_checked",
                "can_be_approved",
                "follower_num",
                "gmv",
                "item_sold",
                "fulfillment_rate",
                "creator_type",
            )
            for row in result.get("rows") or []:
                apply_id = str(row.get("apply_id") or "")
                if not apply_id:
                    continue
                metrics = {key: row.get(key) for key in metric_keys if key in row}
                candidate = existing.get(apply_id) or AutoApprovalCandidate(
                    preview_id=preview_id,
                    apply_id=apply_id,
                )
                candidate.creator_id = str(row.get("creator_id") or "")
                candidate.creator_name = str(row.get("creator_name") or "")
                candidate.product_id = str(row.get("product_id") or "")
                candidate.overall = str(row.get("custom_overall") or "")
                candidate.content_verdict = str(row.get("content_verdict") or "")
                candidate.custom_eligible = bool(row.get("custom_eligible"))
                candidate.blocked = bool(row.get("blocked"))
                candidate.checks_json = json.dumps(
                    row.get("custom_checks") or [], ensure_ascii=False
                )
                candidate.safety_blocks_json = json.dumps(
                    row.get("safety_blocks") or [], ensure_ascii=False
                )
                candidate.metrics_json = json.dumps(metrics, ensure_ascii=False, default=str)
                candidate.content_status = str(row.get("content_review_status") or "")
                candidate.content_reason = str(row.get("content_review_reason") or "")
                candidate.content_evidence_path = str(
                    row.get("content_review_evidence_path") or ""
                )
                candidate.content_related_count = int(
                    row.get("content_review_related_count") or 0
                )
                candidate.content_complete = bool(row.get("content_review_complete"))
                candidate.observed_at = str(row.get("observed_at") or "")
                if candidate.id is None:
                    session.add(candidate)
        session.commit()


def sync_execution_result(session_factory, execution_id: str, *, status: str, error_code: str = "", error_summary: str = "") -> None:
    """任务完成后同步执行明细（子进程 handler 调用）。"""
    with session_factory() as session:
        execution = session.get(AutoApprovalExecution, execution_id)
        if execution is None:
            return
        execution.status = status
        execution.error_code = error_code
        execution.error_summary = error_summary
        execution.finished_at = _utc_now()
        if status in {"completed", "needs_reconcile"}:
            try:
                result = json.loads(Path(execution.result_path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                session.commit()
                return
            items = {
                item.apply_id: item
                for item in session.scalars(
                    select(AutoApprovalExecutionItem).where(
                        AutoApprovalExecutionItem.execution_id == execution_id
                    )
                ).all()
            }
            for row in result.get("items") or []:
                apply_id = str(row.get("apply_id") or "")
                item = items.get(apply_id)
                if item is None:
                    continue
                item.approve_status = str(row.get("approve_status") or "unknown")
                item.approve_error = str(row.get("approve_error") or "")
                item.action = str(row.get("action") or "")
                item.platform_confirmation_status = str(
                    row.get("platform_confirmation_status") or ""
                )
                item.feishu_relation_status = str(row.get("feishu_relation_status") or "")
                item.feishu_record_id = str(row.get("feishu_record_id") or "")
                item.feishu_error = str(row.get("feishu_error") or "")
                item.approved_at = str(row.get("approved_at") or "")
            unknown_count = sum(
                1 for item in items.values() if item.approve_status == "unknown"
            )
            if unknown_count:
                execution.status = "needs_reconcile"
        session.commit()


def sync_reconcile_result(
    session_factory,
    execution_id: str,
    *,
    reconcile_result_path: str,
    status: str,
    error_code: str = "",
    error_summary: str = "",
) -> None:
    """核对任务完成后同步执行明细（平台确认/飞书补写/订单回填状态）。"""
    with session_factory() as session:
        execution = session.get(AutoApprovalExecution, execution_id)
        if execution is None:
            return
        if status != "completed":
            # 核对失败不改写执行批次终态；执行批次本身已完成，核对可重跑。
            session.commit()
            return
        try:
            result = json.loads(Path(reconcile_result_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            session.commit()
            return
        items = {
            item.apply_id: item
            for item in session.scalars(
                select(AutoApprovalExecutionItem).where(
                    AutoApprovalExecutionItem.execution_id == execution_id
                )
            ).all()
        }
        for row in result.get("items") or []:
            apply_id = str(row.get("apply_id") or "")
            item = items.get(apply_id)
            if item is None:
                continue
            item.platform_confirmation_status = str(
                row.get("platform_confirmation_status")
                or row.get("approve_confirmation")
                or ""
            )
            relation_status = str(
                row.get("feishu_relation_status") or row.get("feishu_status") or ""
            )
            if relation_status:
                item.feishu_relation_status = relation_status
            record_id = str(row.get("feishu_record_id") or "")
            if record_id:
                item.feishu_record_id = record_id
            feishu_error = str(row.get("feishu_error") or "")
            if feishu_error:
                item.feishu_error = feishu_error
        session.commit()


def job_type_of(request_payload: dict[str, Any]) -> str:
    return str(request_payload.get("job_type") or "")
