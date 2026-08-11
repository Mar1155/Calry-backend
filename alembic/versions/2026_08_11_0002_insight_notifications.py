"""add insight notification preferences, delivery ledger, analytics, lifecycle

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("proactive_insights", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "insight_notification_preferences",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("proactive_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("daily_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("weekly_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("quiet_hours_start", sa.String(5), server_default="21:00", nullable=False),
        sa.Column("quiet_hours_end", sa.String(5), server_default="08:00", nullable=False),
        sa.Column("timezone", sa.String(64), server_default="UTC", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "insight_notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("insight_id", sa.String(64), sa.ForeignKey("proactive_insights.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(30), server_default="fcm", nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("suppression_reason", sa.String(80), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("insight_id", name="uq_insight_notification_delivery_insight"),
        sa.UniqueConstraint("idempotency_key", name="uq_insight_notification_delivery_key"),
    )
    op.create_index("ix_insight_notification_deliveries_user_id", "insight_notification_deliveries", ["user_id"])
    op.create_index("ix_insight_notification_deliveries_status", "insight_notification_deliveries", ["status"])
    op.create_index("ix_insight_notification_deliveries_due", "insight_notification_deliveries", ["status", "scheduled_for"])

    op.create_table(
        "insight_analytics_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("insight_id", sa.String(64), sa.ForeignKey("proactive_insights.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_name", sa.String(50), nullable=False),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_insight_analytics_event_id"),
    )
    op.create_index("ix_insight_analytics_events_user_id", "insight_analytics_events", ["user_id"])
    op.create_index("ix_insight_analytics_events_event_name", "insight_analytics_events", ["event_name"])
    op.create_index("ix_insight_analytics_events_metrics", "insight_analytics_events", ["event_name", "created_at"])


def downgrade() -> None:
    op.drop_table("insight_analytics_events")
    op.drop_table("insight_notification_deliveries")
    op.drop_table("insight_notification_preferences")
    op.drop_column("proactive_insights", "superseded_at")
