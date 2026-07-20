"""add versioned insight snapshots

Revision ID: a8b9c0d1e2f3
Revises: f7a8b1c2d3e4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "f7a8b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_insight_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meal_data_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("activity_data_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("hydration_data_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("profile_data_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("target_data_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("weight_data_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ai_accuracy_data_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("logging_behavior_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_insight_versions_user_id", "user_insight_versions", ["user_id"], unique=True)

    op.create_table(
        "detected_patterns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("detector_id", sa.String(80), nullable=False),
        sa.Column("detector_version", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("source_versions_json", sa.JSON(), nullable=False),
        sa.Column("pattern_key", sa.String(100), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("novelty_score", sa.Float(), nullable=False),
        sa.Column("effect_size", sa.Float(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("comparison_status", sa.String(32), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_detected_patterns_user_id", "detected_patterns", ["user_id"])
    op.create_index("ix_detected_patterns_detector_id", "detected_patterns", ["detector_id"])
    op.create_index("ix_detected_patterns_scope", "detected_patterns", ["scope"])
    op.create_index("ix_detected_patterns_payload_hash", "detected_patterns", ["payload_hash"])
    op.create_index(
        "ix_detected_patterns_active",
        "detected_patterns",
        ["user_id", "scope", "detector_id", "superseded_at"],
    )

    op.create_table(
        "insight_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("generation_key", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("insight_scope", sa.String(40), nullable=False),
        sa.Column("locale", sa.String(12), server_default="en", nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("source_data_version", sa.String(64), nullable=False),
        sa.Column("source_versions_json", sa.JSON(), nullable=False),
        sa.Column("detector_version", sa.String(40), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("model_version", sa.String(120), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("insights_json", sa.JSON(), nullable=False),
        sa.Column("ranking_metadata_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("generation_key", name="uq_insight_snapshot_generation_key"),
    )
    op.create_index("ix_insight_snapshots_snapshot_id", "insight_snapshots", ["snapshot_id"], unique=True)
    op.create_index("ix_insight_snapshots_user_id", "insight_snapshots", ["user_id"])
    op.create_index("ix_insight_snapshots_insight_scope", "insight_snapshots", ["insight_scope"])
    op.create_index("ix_insight_snapshots_status", "insight_snapshots", ["status"])
    op.create_index(
        "ix_insight_snapshots_latest",
        "insight_snapshots",
        ["user_id", "insight_scope", "locale", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("insight_snapshots")
    op.drop_table("detected_patterns")
    op.drop_table("user_insight_versions")
