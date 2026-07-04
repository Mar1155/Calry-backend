"""add revenuecat events ledger, subscriber snapshots and user premium metadata

Revision ID: b3c4d5e6f7a8
Revises: e7f8a9b0c1d2
Create Date: 2026-07-04 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("premium_store", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("premium_product_id", sa.String(length=255), nullable=True))

    op.create_table(
        "revenuecat_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("app_user_id", sa.String(length=255), nullable=True),
        sa.Column("product_id", sa.String(length=255), nullable=True),
        sa.Column("entitlement_ids", sa.JSON(), nullable=True),
        sa.Column("transaction_id", sa.String(length=255), nullable=True),
        sa.Column("original_transaction_id", sa.String(length=255), nullable=True),
        sa.Column("expiration_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("environment", sa.String(length=50), nullable=True),
        sa.Column("store", sa.String(length=50), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("processing_status", sa.String(length=50), nullable=False, server_default="received"),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_revenuecat_events_event_id", "revenuecat_events", ["event_id"], unique=True)
    op.create_index("ix_revenuecat_events_app_user_id", "revenuecat_events", ["app_user_id"])

    op.create_table(
        "revenuecat_subscriber_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("app_user_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("entitlement_active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_revenuecat_subscriber_snapshots_app_user_id",
        "revenuecat_subscriber_snapshots",
        ["app_user_id"],
    )
    op.create_index(
        "ix_revenuecat_subscriber_snapshots_user_id",
        "revenuecat_subscriber_snapshots",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_revenuecat_subscriber_snapshots_user_id", table_name="revenuecat_subscriber_snapshots")
    op.drop_index("ix_revenuecat_subscriber_snapshots_app_user_id", table_name="revenuecat_subscriber_snapshots")
    op.drop_table("revenuecat_subscriber_snapshots")
    op.drop_index("ix_revenuecat_events_app_user_id", table_name="revenuecat_events")
    op.drop_index("ix_revenuecat_events_event_id", table_name="revenuecat_events")
    op.drop_table("revenuecat_events")
    op.drop_column("users", "premium_product_id")
    op.drop_column("users", "premium_store")
