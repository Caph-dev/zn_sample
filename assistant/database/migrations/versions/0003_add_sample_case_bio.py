"""Add creator bio to sample cases.

Follow-up language resolution falls back to the creator profile bio
(detect_creator_lang) when the Feishu language field is empty. The bio is
fetched once from the creator detail page and cached on the sample case.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003_add_sample_case_bio"
down_revision = "0002_add_shipment_sync_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sample_cases",
        sa.Column("bio", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sample_cases", "bio")
