"""Follow-up status model: platform status, actions, content evidence.

Removes unused current_state / shipment status_code, stops using the
1970-01-01 sentinel for delivery-date confirmation, and adds the fields
needed for SOP 2 terminal states.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_followup_status_model"
down_revision = "0003_add_sample_case_bio"
branch_labels = None
depends_on = None


PLATFORM_STATUS_BY_CODE = {
    10: ("pending_review", "待审核"),
    11: ("pending_review", "待审核"),
    20: ("ready_to_ship", "待发货"),
    30: ("shipped", "已发货"),
    40: ("processing", "处理中"),
}

ACTION_KIND_BY_STAGE = {
    "arrival": "send_message",
    "day_3": "send_message",
    "day_7": "send_message",
    "day_10_list": "list_only",
    "unfulfilled": "mark_unfulfilled",
    "confirm_delivery_time": "confirm_delivery_date",
    "content_found": "acknowledge_content",
}


def upgrade() -> None:
    op.add_column(
        "sample_cases",
        sa.Column("platform_status", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "sample_cases",
        sa.Column("platform_status_label", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "sample_cases",
        sa.Column("platform_status_source", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "sample_cases",
        sa.Column("platform_status_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "sample_cases",
        sa.Column("platform_status_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sample_cases",
        sa.Column("feishu_cooperation_status", sa.String(), nullable=False, server_default=""),
    )
    op.drop_column("sample_cases", "current_state")

    op.drop_column("shipments", "status_code")
    op.drop_column("shipment_snapshots", "status_code")

    op.add_column(
        "followup_tasks",
        sa.Column("action_kind", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "followup_tasks",
        sa.Column("review_reason", sa.String(), nullable=False, server_default=""),
    )
    with op.batch_alter_table("followup_tasks") as batch_op:
        batch_op.alter_column("scheduled_for", existing_type=sa.Date(), nullable=True)

    op.create_table(
        "content_evidences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_case_id", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("content_status", sa.String(), nullable=False),
        sa.Column("content_url", sa.String(), nullable=False),
        sa.Column("content_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("confirmed_by", sa.String(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("feishu_write_status", sa.String(), nullable=False),
        sa.Column("feishu_write_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["sample_case_id"], ["sample_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    connection = op.get_bind()
    for code, (status, label) in PLATFORM_STATUS_BY_CODE.items():
        connection.execute(
            sa.text(
                "UPDATE sample_cases SET platform_status = :status, "
                "platform_status_label = :label, platform_status_source = 'legacy_curr_status', "
                "platform_status_observed_at = last_seen_at "
                "WHERE curr_status = :code"
            ),
            {"status": status, "label": label, "code": code},
        )
    connection.execute(
        sa.text(
            "UPDATE sample_cases SET platform_status = 'unknown', "
            "platform_status_label = '未知' "
            "WHERE platform_status = '' OR platform_status IS NULL"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE followup_tasks SET scheduled_for = NULL "
            "WHERE stage = 'confirm_delivery_time' AND scheduled_for = '1970-01-01'"
        )
    )
    for stage, action_kind in ACTION_KIND_BY_STAGE.items():
        connection.execute(
            sa.text(
                "UPDATE followup_tasks SET action_kind = :action_kind "
                "WHERE stage = :stage AND (action_kind = '' OR action_kind IS NULL)"
            ),
            {"action_kind": action_kind, "stage": stage},
        )
    connection.execute(
        sa.text(
            "UPDATE followup_tasks SET review_reason = 'missing_delivery_time' "
            "WHERE stage = 'confirm_delivery_time' AND status = 'needs_review' "
            "AND (review_reason = '' OR review_reason IS NULL)"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE sample_cases SET creator_type = 'both' "
            "WHERE is_video_creator = '是' AND is_live_creator = '是' "
            "AND (creator_type = '' OR creator_type IS NULL)"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE followup_tasks SET creator_type = 'both' "
            "WHERE sample_case_id IN ("
            "SELECT id FROM sample_cases "
            "WHERE is_video_creator = '是' AND is_live_creator = '是'"
            ") AND (creator_type = '' OR creator_type IS NULL)"
        )
    )

def downgrade() -> None:
    op.drop_table("content_evidences")
    with op.batch_alter_table("followup_tasks") as batch_op:
        batch_op.alter_column("scheduled_for", existing_type=sa.Date(), nullable=False)
    op.drop_column("followup_tasks", "review_reason")
    op.drop_column("followup_tasks", "action_kind")
    op.add_column(
        "shipment_snapshots",
        sa.Column("status_code", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "shipments",
        sa.Column("status_code", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "sample_cases",
        sa.Column("current_state", sa.String(), nullable=False, server_default=""),
    )
    op.drop_column("sample_cases", "feishu_cooperation_status")
    op.drop_column("sample_cases", "platform_status_observed_at")
    op.drop_column("sample_cases", "platform_status_stale")
    op.drop_column("sample_cases", "platform_status_source")
    op.drop_column("sample_cases", "platform_status_label")
    op.drop_column("sample_cases", "platform_status")
