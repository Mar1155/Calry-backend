"""add proactive insight event inbox and persistent diary

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proactive_insight_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(80), nullable=False),
        sa.Column("affected_date", sa.Date(), nullable=True),
        sa.Column("source_versions_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_proactive_insight_event_id"),
    )
    op.create_index("ix_proactive_insight_events_user_id", "proactive_insight_events", ["user_id"])
    op.create_index("ix_proactive_insight_events_trigger", "proactive_insight_events", ["trigger"])
    op.create_index("ix_proactive_insight_events_status", "proactive_insight_events", ["status"])
    op.create_index(
        "ix_proactive_insight_events_pending",
        "proactive_insight_events",
        ["status", "created_at"],
    )

    op.create_table(
        "proactive_insights",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("candidate_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("trigger", sa.String(80), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("novelty", sa.Float(), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("significance", sa.Float(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column(
            "related_insight_id",
            sa.String(64),
            sa.ForeignKey("proactive_insights.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notification_score", sa.Float(), nullable=False),
        sa.Column("notification_status", sa.String(20), nullable=False),
        sa.Column("notification_ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_version", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_id", name="uq_proactive_insight_candidate"),
    )
    op.create_index("ix_proactive_insights_user_id", "proactive_insights", ["user_id"])
    op.create_index("ix_proactive_insights_type", "proactive_insights", ["type"])
    op.create_index("ix_proactive_insights_category", "proactive_insights", ["category"])
    op.create_index("ix_proactive_insights_trigger", "proactive_insights", ["trigger"])
    op.create_index("ix_proactive_insights_notification_status", "proactive_insights", ["notification_status"])
    op.create_index("ix_proactive_insights_diary", "proactive_insights", ["user_id", "created_at"])
    op.create_index(
        "ix_proactive_insights_dedup",
        "proactive_insights",
        ["user_id", "dedup_key", "created_at"],
    )
    op.create_index(
        "ix_proactive_insights_notification",
        "proactive_insights",
        ["notification_status", "notification_ready_at"],
    )


def downgrade() -> None:
    op.drop_table("proactive_insights")
    op.drop_table("proactive_insight_events")
