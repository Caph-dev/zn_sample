"""Add the follow-up preview timestamp.

The detail page shows the outcome of the last preview (and any failure
reason). The timestamp of that attempt is stored separately so the page can
show when it happened without reusing ``updated_at``, which changes on every
unrelated write to the task.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006_followup_previewed_at"
down_revision = "0005_auto_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "followup_tasks",
        sa.Column("previewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("followup_tasks", "previewed_at")
