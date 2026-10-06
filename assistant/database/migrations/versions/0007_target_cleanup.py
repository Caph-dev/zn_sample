"""Frozen persistence schema for target-invitation cleanup batches."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_target_cleanup"
down_revision = "0006_followup_previewed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "target_cleanup_batches",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.String(), nullable=False),
        sa.Column("store_name", sa.String(), nullable=False),
        sa.Column("shop_id", sa.String(), nullable=False),
        sa.Column("shop_region", sa.String(), nullable=False),
        sa.Column("frozen_json", sa.Text(), nullable=False),
        sa.Column("preview_status", sa.String(), nullable=False),
        sa.Column("execute_status", sa.String(), nullable=False),
        sa.Column("preview_job_id", sa.String(), nullable=False),
        sa.Column("execute_job_id", sa.String(), nullable=True),
        sa.Column("preview_idempotency_key", sa.String(), nullable=True),
        sa.Column("execute_idempotency_key", sa.String(), nullable=True),
        sa.Column("preview_request_fingerprint", sa.String(), nullable=False),
        sa.Column("execute_request_fingerprint", sa.String(), nullable=False),
        sa.Column("snapshot_path", sa.String(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(), nullable=False),
        sa.Column("artifacts_json", sa.Text(), nullable=False),
        sa.Column("execute_envelope_json", sa.Text(), nullable=False),
        sa.Column("scan_complete", sa.Boolean(), nullable=False),
        sa.Column("stop_reason", sa.String(), nullable=False),
        sa.Column("pages_scanned", sa.Integer(), nullable=False),
        sa.Column("scan_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("nonzero_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("preview_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preview_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execute_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execute_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["preview_job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["execute_job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execute_job_id"),
        sa.UniqueConstraint("preview_idempotency_key"),
        sa.UniqueConstraint("execute_idempotency_key"),
    )
    op.create_index("ix_target_cleanup_batches_store_id", "target_cleanup_batches", ["store_id"])
    op.create_table(
        "target_cleanup_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("store_id", sa.String(), nullable=False),
        sa.Column("invitation_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("row_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["batch_id"], ["target_cleanup_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "invitation_id"),
    )
    op.create_index("ix_target_cleanup_items_store_id", "target_cleanup_items", ["store_id"])
    op.create_index("ix_target_cleanup_items_invitation_id", "target_cleanup_items", ["invitation_id"])


def downgrade() -> None:
    op.drop_index("ix_target_cleanup_items_invitation_id", table_name="target_cleanup_items")
    op.drop_index("ix_target_cleanup_items_store_id", table_name="target_cleanup_items")
    op.drop_table("target_cleanup_items")
    op.drop_index("ix_target_cleanup_batches_store_id", table_name="target_cleanup_batches")
    op.drop_table("target_cleanup_batches")
