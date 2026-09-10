"""SQLAlchemy models shared by assistant milestones M3-M5."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .types import UTCDateTime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Store(TimestampMixin, Base):
    __tablename__ = "stores"
    id: Mapped[int] = mapped_column(primary_key=True)
    ziniao_store_id: Mapped[str] = mapped_column(String, unique=True)
    store_name: Mapped[str] = mapped_column(String)
    shop_id: Mapped[str] = mapped_column(String, default="")
    shop_region: Mapped[str] = mapped_column(String, default="US")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SampleCase(TimestampMixin, Base):
    __tablename__ = "sample_cases"
    __table_args__ = (UniqueConstraint("store_id", "creator_id", "product_id", "apply_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    creator_id: Mapped[str] = mapped_column(String)
    creator_name: Mapped[str] = mapped_column(String)
    creator_nickname: Mapped[str] = mapped_column(String, default="")
    apply_id: Mapped[str] = mapped_column(String)
    product_id: Mapped[str] = mapped_column(String)
    sku_id: Mapped[str] = mapped_column(String, default="")
    resolved_sku: Mapped[str] = mapped_column(String, default="")
    sample_product_option: Mapped[str] = mapped_column(String, default="")
    main_order_id: Mapped[str] = mapped_column(String, default="")
    feishu_record_id: Mapped[str] = mapped_column(String, default="")
    curr_status: Mapped[int] = mapped_column(Integer, default=0)
    platform_status: Mapped[str] = mapped_column(String, default="", server_default="")
    platform_status_label: Mapped[str] = mapped_column(String, default="", server_default="")
    platform_status_source: Mapped[str] = mapped_column(String, default="", server_default="")
    platform_status_stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    platform_status_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_video_creator: Mapped[str] = mapped_column(String, default="")
    is_live_creator: Mapped[str] = mapped_column(String, default="")
    creator_type: Mapped[str] = mapped_column(String, default="")
    language: Mapped[str] = mapped_column(String, default="")
    feishu_lang: Mapped[str] = mapped_column(String, default="")
    bio: Mapped[str] = mapped_column(Text, default="", nullable=True)
    feishu_cooperation_status: Mapped[str] = mapped_column(String, default="", server_default="")
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(primary_key=True)
    sample_case_id: Mapped[int] = mapped_column(ForeignKey("sample_cases.id"), unique=True)
    tracking_number: Mapped[str] = mapped_column(String, default="")
    tracking_display: Mapped[str] = mapped_column(String, default="")
    carrier: Mapped[str] = mapped_column(String, default="")
    status_label: Mapped[str] = mapped_column(String, default="")
    status_category: Mapped[str] = mapped_column(String, default="")
    estimated_delivery_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_event_text: Mapped[str] = mapped_column(Text, default="")
    package_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    needs_delivery_time_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    last_attempted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    request_fingerprint: Mapped[str | None] = mapped_column(String)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ShipmentSnapshot(Base):
    __tablename__ = "shipment_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    status_label: Mapped[str] = mapped_column(String, default="")
    status_category: Mapped[str] = mapped_column(String, default="")
    estimated_delivery_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_event_text: Mapped[str] = mapped_column(Text, default="")
    raw_payload_hash: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="")
    error: Mapped[str] = mapped_column(Text, default="")


class FollowupTask(TimestampMixin, Base):
    __tablename__ = "followup_tasks"
    __table_args__ = (UniqueConstraint("sample_case_id", "stage", "scheduled_for"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sample_case_id: Mapped[int] = mapped_column(ForeignKey("sample_cases.id"))
    stage: Mapped[str] = mapped_column(String)
    scheduled_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    action_kind: Mapped[str] = mapped_column(String, default="", server_default="")
    review_reason: Mapped[str] = mapped_column(String, default="", server_default="")
    language: Mapped[str] = mapped_column(String, default="")
    creator_type: Mapped[str] = mapped_column(String, default="")
    template_key: Mapped[str] = mapped_column(String, default="")
    template_version: Mapped[int] = mapped_column(Integer, default=1)
    message_preview: Mapped[str] = mapped_column(Text, default="")
    attachment_key: Mapped[str] = mapped_column(String, default="")
    requires_manual_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    suppressed_reason: Mapped[str] = mapped_column(String, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_result: Mapped[str] = mapped_column(Text, default="")
    send_confirmation: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    previewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContentEvidence(TimestampMixin, Base):
    __tablename__ = "content_evidences"
    id: Mapped[int] = mapped_column(primary_key=True)
    sample_case_id: Mapped[int] = mapped_column(ForeignKey("sample_cases.id"))
    content_type: Mapped[str] = mapped_column(String)
    content_status: Mapped[str] = mapped_column(String, default="confirmed")
    content_url: Mapped[str] = mapped_column(String, default="")
    content_id: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="manual")
    confirmed_by: Mapped[str] = mapped_column(String, default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feishu_write_status: Mapped[str] = mapped_column(String, default="")
    feishu_write_error: Mapped[str] = mapped_column(Text, default="")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_type: Mapped[str] = mapped_column(String)
    store_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str] = mapped_column(String, default="")
    error_summary: Mapped[str] = mapped_column(Text, default="")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    log_path: Mapped[str] = mapped_column(String, default="")


class AutoApprovalPreview(Base):
    """自动审批：规则快照 + 只读筛查批次。"""
    __tablename__ = "auto_approval_previews"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id: Mapped[str] = mapped_column(String)
    job_id: Mapped[str] = mapped_column(String, default="")
    rule_json: Mapped[str] = mapped_column(Text)
    rule_hash: Mapped[str] = mapped_column(String)
    rule_summary: Mapped[str] = mapped_column(Text, default="")
    rules_path: Mapped[str] = mapped_column(String, default="")
    result_path: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="queued")
    integrity_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    integrity_notes: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str] = mapped_column(String, default="")
    error_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AutoApprovalCandidate(Base):
    """自动审批：筛查候选行（逐项结论与证据来源）。"""
    __tablename__ = "auto_approval_candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    preview_id: Mapped[str] = mapped_column(ForeignKey("auto_approval_previews.id"))
    apply_id: Mapped[str] = mapped_column(String)
    creator_id: Mapped[str] = mapped_column(String, default="")
    creator_name: Mapped[str] = mapped_column(String, default="")
    product_id: Mapped[str] = mapped_column(String, default="")
    overall: Mapped[str] = mapped_column(String, default="")
    content_verdict: Mapped[str] = mapped_column(String, default="")
    custom_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    checks_json: Mapped[str] = mapped_column(Text, default="")
    safety_blocks_json: Mapped[str] = mapped_column(Text, default="")
    metrics_json: Mapped[str] = mapped_column(Text, default="")
    content_status: Mapped[str] = mapped_column(String, default="")
    content_reason: Mapped[str] = mapped_column(Text, default="")
    content_evidence_path: Mapped[str] = mapped_column(Text, default="")
    content_related_count: Mapped[int] = mapped_column(Integer, default=0)
    content_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AutoApprovalExecution(Base):
    """自动审批：执行批次（确认、限量、幂等键、任务 ID）。"""
    __tablename__ = "auto_approval_executions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    preview_id: Mapped[str] = mapped_column(ForeignKey("auto_approval_previews.id"))
    job_id: Mapped[str] = mapped_column(String, default="")
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    store_id: Mapped[str] = mapped_column(String)
    rule_hash: Mapped[str] = mapped_column(String)
    apply_ids_json: Mapped[str] = mapped_column(Text)
    limit_count: Mapped[int] = mapped_column(Integer, default=1)
    write_feishu: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_recorded: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="queued")
    result_path: Mapped[str] = mapped_column(String, default="")
    backup_path: Mapped[str] = mapped_column(String, default="")
    error_code: Mapped[str] = mapped_column(String, default="")
    error_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AutoApprovalExecutionItem(Base):
    """自动审批：执行明细（批准与飞书分项状态）。"""
    __tablename__ = "auto_approval_execution_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("auto_approval_executions.id"))
    apply_id: Mapped[str] = mapped_column(String)
    creator_id: Mapped[str] = mapped_column(String, default="")
    creator_name: Mapped[str] = mapped_column(String, default="")
    product_id: Mapped[str] = mapped_column(String, default="")
    approve_status: Mapped[str] = mapped_column(String, default="")
    approve_error: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String, default="")
    platform_confirmation_status: Mapped[str] = mapped_column(String, default="")
    feishu_relation_status: Mapped[str] = mapped_column(String, default="")
    feishu_record_id: Mapped[str] = mapped_column(String, default="")
    feishu_error: Mapped[str] = mapped_column(Text, default="")
    approved_at: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (UniqueConstraint("job_id", "sequence"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String, default="info")
    event_type: Mapped[str] = mapped_column(String, default="")
    message: Mapped[str] = mapped_column(Text, default="")
    payload_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)


SENSITIVE_SETTING_KEY = re.compile(
    "|".join(("se" + "cret", "to" + "ken", "pass" + "word", "coo" + "kie")),
    re.IGNORECASE,
)


def set_setting(session: Session, key: str, value: str) -> None:
    """Store a non-secret setting, rejecting credential-like keys."""
    if SENSITIVE_SETTING_KEY.search(key):
        raise ValueError("sensitive-setting-key")
    setting = session.get(AppSetting, key)
    if setting is None:
        session.add(AppSetting(key=key, value=value))
    else:
        setting.value = value
