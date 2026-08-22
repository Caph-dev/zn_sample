"""Create the complete M3 schema."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ziniao_store_id", sa.String(), nullable=False),
        sa.Column("store_name", sa.String(), nullable=False),
        sa.Column("shop_id", sa.String(), nullable=False),
        sa.Column("shop_region", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ziniao_store_id"),
    )
    op.create_table(
        "sample_cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("creator_id", sa.String(), nullable=False),
        sa.Column("creator_name", sa.String(), nullable=False),
        sa.Column("creator_nickname", sa.String(), nullable=False),
        sa.Column("apply_id", sa.String(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("sku_id", sa.String(), nullable=False),
        sa.Column("resolved_sku", sa.String(), nullable=False),
        sa.Column("sample_product_option", sa.String(), nullable=False),
        sa.Column("main_order_id", sa.String(), nullable=False),
        sa.Column("feishu_record_id", sa.String(), nullable=False),
        sa.Column("curr_status", sa.Integer(), nullable=False),
        sa.Column("is_video_creator", sa.String(), nullable=False),
        sa.Column("is_live_creator", sa.String(), nullable=False),
        sa.Column("creator_type", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("feishu_lang", sa.String(), nullable=False),
        sa.Column("current_state", sa.String(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", "creator_id", "product_id", "apply_id"),
    )
    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_case_id", sa.Integer(), nullable=False),
        sa.Column("tracking_number", sa.String(), nullable=False),
        sa.Column("tracking_display", sa.String(), nullable=False),
        sa.Column("carrier", sa.String(), nullable=False),
        sa.Column("status_code", sa.String(), nullable=False),
        sa.Column("status_label", sa.String(), nullable=False),
        sa.Column("status_category", sa.String(), nullable=False),
        sa.Column("estimated_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_text", sa.Text(), nullable=False),
        sa.Column("package_count", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("needs_delivery_time_confirmation", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["sample_case_id"], ["sample_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sample_case_id"),
    )
    op.create_table(
        "shipment_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_code", sa.String(), nullable=False),
        sa.Column("status_label", sa.String(), nullable=False),
        sa.Column("status_category", sa.String(), nullable=False),
        sa.Column("estimated_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_text", sa.Text(), nullable=False),
        sa.Column("raw_payload_hash", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "followup_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_case_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("scheduled_for", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("creator_type", sa.String(), nullable=False),
        sa.Column("template_key", sa.String(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("message_preview", sa.Text(), nullable=False),
        sa.Column("attachment_key", sa.String(), nullable=False),
        sa.Column("requires_manual_confirmation", sa.Boolean(), nullable=False),
        sa.Column("suppressed_reason", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("send_result", sa.Text(), nullable=False),
        sa.Column("send_confirmation", sa.Text(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["sample_case_id"], ["sample_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sample_case_id", "stage", "scheduled_for"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("store_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("progress_message", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=False),
        sa.Column("log_path", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "sequence"),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("job_events")
    op.drop_table("jobs")
    op.drop_table("followup_tasks")
    op.drop_table("shipment_snapshots")
    op.drop_table("shipments")
    op.drop_table("sample_cases")
    op.drop_table("stores")
