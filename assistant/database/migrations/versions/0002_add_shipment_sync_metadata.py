"""Add shipment sync metadata columns.

Tracks the last sync attempt, last successful sync, the normalized request
fingerprint, and the last error timestamp so the incremental refresh policy
can distinguish "recently attempted" from "recently succeeded".
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002_add_shipment_sync_metadata"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shipments",
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "shipments",
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "shipments",
        sa.Column("request_fingerprint", sa.String(), nullable=True),
    )
    op.add_column(
        "shipments",
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shipments", "last_error_at")
    op.drop_column("shipments", "request_fingerprint")
    op.drop_column("shipments", "last_successful_sync_at")
    op.drop_column("shipments", "last_attempted_at")
