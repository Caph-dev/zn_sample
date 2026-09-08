"""Auto approval: rule snapshots, screening batches, candidates, executions.

Adds the four persistence tables for the custom auto-approval page:
previews (rule snapshot + screening batch), candidates (per-row verdicts),
executions (confirmed batches with idempotency keys) and execution items
(per-row approval / Feishu outcomes).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005_auto_approval"
down_revision = "0004_followup_status_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auto_approval_previews",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("store_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("rule_json", sa.Text(), nullable=False),
        sa.Column("rule_hash", sa.String(), nullable=False),
        sa.Column("rule_summary", sa.Text(), nullable=False),
        sa.Column("rules_path", sa.String(), nullable=False),
        sa.Column("result_path", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("integrity_complete", sa.Boolean(), nullable=False),
        sa.Column("integrity_notes", sa.Text(), nullable=False),
        sa.Column("stats", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "auto_approval_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("preview_id", sa.String(), nullable=False),
        sa.Column("apply_id", sa.String(), nullable=False),
        sa.Column("creator_id", sa.String(), nullable=False),
        sa.Column("creator_name", sa.String(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("overall", sa.String(), nullable=False),
        sa.Column("content_verdict", sa.String(), nullable=False),
        sa.Column("custom_eligible", sa.Boolean(), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column("safety_blocks_json", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("content_status", sa.String(), nullable=False),
        sa.Column("content_reason", sa.Text(), nullable=False),
        sa.Column("content_evidence_path", sa.Text(), nullable=False),
        sa.Column("content_related_count", sa.Integer(), nullable=False),
        sa.Column("content_complete", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["preview_id"], ["auto_approval_previews.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auto_approval_candidates_preview_apply",
        "auto_approval_candidates",
        ["preview_id", "apply_id"],
        unique=True,
    )

    op.create_table(
        "auto_approval_executions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("preview_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("store_id", sa.String(), nullable=False),
        sa.Column("rule_hash", sa.String(), nullable=False),
        sa.Column("apply_ids_json", sa.Text(), nullable=False),
        sa.Column("limit_count", sa.Integer(), nullable=False),
        sa.Column("write_feishu", sa.Boolean(), nullable=False),
        sa.Column("confirmation_recorded", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result_path", sa.String(), nullable=False),
        sa.Column("backup_path", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["preview_id"], ["auto_approval_previews.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_table(
        "auto_approval_execution_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=False),
        sa.Column("apply_id", sa.String(), nullable=False),
        sa.Column("creator_id", sa.String(), nullable=False),
        sa.Column("creator_name", sa.String(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("approve_status", sa.String(), nullable=False),
        sa.Column("approve_error", sa.Text(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("platform_confirmation_status", sa.String(), nullable=False),
        sa.Column("feishu_relation_status", sa.String(), nullable=False),
        sa.Column("feishu_record_id", sa.String(), nullable=False),
        sa.Column("feishu_error", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["auto_approval_executions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auto_approval_execution_items_execution_apply",
        "auto_approval_execution_items",
        ["execution_id", "apply_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auto_approval_execution_items_execution_apply",
        table_name="auto_approval_execution_items",
    )
    op.drop_table("auto_approval_execution_items")
    op.drop_table("auto_approval_executions")
    op.drop_index(
        "ix_auto_approval_candidates_preview_apply",
        table_name="auto_approval_candidates",
    )
    op.drop_table("auto_approval_candidates")
    op.drop_table("auto_approval_previews")
