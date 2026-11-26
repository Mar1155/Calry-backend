"""push foundations: multi-device tokens, type-neutral delivery ledger, opt-out defaults

Revision ID: push_foundations_0001
Revises: user_target_weight_0001
Create Date: 2026-11-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "push_foundations_0001"
down_revision: str | None = "user_target_weight_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_devices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(512), nullable=False),
        sa.Column("platform", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token", name="uq_push_device_token"),
    )
    op.create_index("ix_push_devices_user_id", "push_devices", ["user_id"])
    # One row per distinct legacy token; if two accounts somehow share one, the
    # most recently updated account keeps it.
    op.execute(
        """
        INSERT INTO push_devices (user_id, token, created_at, last_seen_at)
        SELECT DISTINCT ON (fcm_token) id, fcm_token, now(), now()
        FROM users
        WHERE fcm_token IS NOT NULL
        ORDER BY fcm_token, updated_at DESC NULLS LAST
        """
    )

    op.alter_column("insight_notification_deliveries", "insight_id", existing_type=sa.String(64), nullable=True)
    op.add_column(
        "insight_notification_deliveries",
        sa.Column("notification_type", sa.String(40), server_default="proactive_insight", nullable=False),
    )
    op.add_column(
        "insight_notification_deliveries",
        sa.Column("category", sa.String(40), server_default="insight", nullable=False),
    )
    op.add_column(
        "insight_notification_deliveries",
        sa.Column("priority", sa.String(16), server_default="normal", nullable=False),
    )
    op.add_column("insight_notification_deliveries", sa.Column("title", sa.String(120), nullable=True))
    op.add_column("insight_notification_deliveries", sa.Column("body", sa.String(240), nullable=True))
    op.add_column(
        "insight_notification_deliveries",
        sa.Column("data_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column(
        "insight_notification_deliveries",
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_insight_notification_deliveries_user_sent",
        "insight_notification_deliveries",
        ["user_id", "sent_at"],
    )
    # Weekly insight pushes were already outside the daily cap.
    op.execute(
        """
        UPDATE insight_notification_deliveries AS d
        SET priority = 'digest'
        FROM proactive_insights AS p
        WHERE p.id = d.insight_id
          AND p.evidence_json -> 'evidence' ->> 'source_trigger' = 'WeeklyEvaluation'
        """
    )

    op.add_column(
        "insight_notification_preferences",
        sa.Column("disabled_categories", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )
    op.alter_column(
        "insight_notification_preferences",
        "proactive_enabled",
        existing_type=sa.Boolean(),
        server_default=sa.true(),
    )
    op.alter_column(
        "insight_notification_preferences",
        "quiet_hours_start",
        existing_type=sa.String(5),
        server_default="22:00",
    )
    # Rows created lazily with the old opt-in default never reflected a choice.
    # Keep every explicit choice the person made in settings.
    op.execute(
        """
        UPDATE insight_notification_preferences AS p
        SET proactive_enabled = true
        WHERE p.proactive_enabled = false
          AND NOT EXISTS (
            SELECT 1 FROM insight_analytics_events AS e
            WHERE e.user_id = p.user_id
              AND e.event_name = 'notification_preferences_changed'
              AND CAST(e.metadata_json AS TEXT) LIKE '%"proactive_enabled"%'
          )
        """
    )
    op.execute(
        """
        UPDATE insight_notification_preferences AS p
        SET quiet_hours_start = '22:00'
        WHERE p.quiet_hours_start = '21:00'
          AND NOT EXISTS (
            SELECT 1 FROM insight_analytics_events AS e
            WHERE e.user_id = p.user_id
              AND e.event_name = 'notification_preferences_changed'
              AND CAST(e.metadata_json AS TEXT) LIKE '%"quiet_hours_start"%'
          )
        """
    )


def downgrade() -> None:
    op.alter_column(
        "insight_notification_preferences",
        "quiet_hours_start",
        existing_type=sa.String(5),
        server_default="21:00",
    )
    op.alter_column(
        "insight_notification_preferences",
        "proactive_enabled",
        existing_type=sa.Boolean(),
        server_default=sa.false(),
    )
    op.drop_column("insight_notification_preferences", "disabled_categories")

    op.drop_index("ix_insight_notification_deliveries_user_sent", table_name="insight_notification_deliveries")
    op.execute("DELETE FROM insight_notification_deliveries WHERE insight_id IS NULL")
    for column in ("opened_at", "data_json", "body", "title", "priority", "category", "notification_type"):
        op.drop_column("insight_notification_deliveries", column)
    op.alter_column("insight_notification_deliveries", "insight_id", existing_type=sa.String(64), nullable=False)

    op.drop_index("ix_push_devices_user_id", table_name="push_devices")
    op.drop_table("push_devices")
