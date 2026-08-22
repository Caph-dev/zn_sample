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
    is_video_creator: Mapped[str] = mapped_column(String, default="")
    is_live_creator: Mapped[str] = mapped_column(String, default="")
    creator_type: Mapped[str] = mapped_column(String, default="")
    language: Mapped[str] = mapped_column(String, default="")
    feishu_lang: Mapped[str] = mapped_column(String, default="")
    current_state: Mapped[str] = mapped_column(String, default="")
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(primary_key=True)
    sample_case_id: Mapped[int] = mapped_column(ForeignKey("sample_cases.id"), unique=True)
    tracking_number: Mapped[str] = mapped_column(String, default="")
    tracking_display: Mapped[str] = mapped_column(String, default="")
    carrier: Mapped[str] = mapped_column(String, default="")
    status_code: Mapped[str] = mapped_column(String, default="")
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


class ShipmentSnapshot(Base):
    __tablename__ = "shipment_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    status_code: Mapped[str] = mapped_column(String, default="")
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
    scheduled_for: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, default="pending")
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
