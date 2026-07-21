"""add AI memory system (beliefs, revisions, moments, evidence, narratives, suppressions)

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_beliefs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("concept", sa.String(60), nullable=False),
        sa.Column("concept_key", sa.String(160), server_default="", nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("value_schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("consistency", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reinforced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_span_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("observation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dispute_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("disputed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("distiller_id", sa.String(80), nullable=False),
        sa.Column("distiller_version", sa.String(40), nullable=False),
        sa.Column("source_versions_json", sa.JSON(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "domain", "concept", "concept_key", name="uq_memory_belief_identity"),
    )
    op.create_index("ix_memory_beliefs_user_id", "memory_beliefs", ["user_id"])
    op.create_index("ix_memory_beliefs_active", "memory_beliefs", ["user_id", "status", "domain"])

    op.create_table(
        "memory_belief_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("belief_id", sa.Integer(), sa.ForeignKey("memory_beliefs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("from_value_json", sa.JSON(), nullable=False),
        sa.Column("to_value_json", sa.JSON(), nullable=False),
        sa.Column("from_confidence", sa.Float(), nullable=False),
        sa.Column("to_confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_memory_belief_revisions_belief_id", "memory_belief_revisions", ["belief_id"])
    op.create_index("ix_memory_belief_revisions_belief", "memory_belief_revisions", ["belief_id", "revision_no"])

    op.create_table(
        "memory_moments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("belief_id", sa.Integer(), sa.ForeignKey("memory_beliefs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("moment_kind", sa.String(30), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("beat_key", sa.String(80), nullable=False),
        sa.Column("fact_json", sa.JSON(), nullable=False),
        sa.Column("confidence_at", sa.Float(), nullable=False),
        sa.Column("evidence_span_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("chapter_key", sa.String(12), nullable=False),
        sa.Column("distiller_version", sa.String(40), nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("belief_id", "beat_key", name="uq_memory_moment_beat"),
    )
    op.create_index("ix_memory_moments_user_id", "memory_moments", ["user_id"])
    op.create_index("ix_memory_moments_belief_id", "memory_moments", ["belief_id"])
    op.create_index("ix_memory_moments_timeline", "memory_moments", ["user_id", "occurred_on", "moment_kind"])
    op.create_index("ix_memory_moments_user_beat", "memory_moments", ["user_id", "beat_key"])

    op.create_table(
        "memory_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("belief_id", sa.Integer(), sa.ForeignKey("memory_beliefs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_type", sa.String(30), nullable=False),
        sa.Column("ref_table", sa.String(40), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("belief_id", "evidence_type", "ref_table", "ref_id", name="uq_memory_evidence_source"),
    )
    op.create_index("ix_memory_evidence_belief_id", "memory_evidence", ["belief_id"])
    op.create_index("ix_memory_evidence_belief", "memory_evidence", ["belief_id", "evidence_type"])

    op.create_table(
        "memory_narratives",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("moment_id", sa.Integer(), sa.ForeignKey("memory_moments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("locale", sa.String(12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("model_version", sa.String(120), server_default="template", nullable=False),
        sa.Column("source", sa.String(20), server_default="template", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("moment_id", "locale", "prompt_version", name="uq_memory_narrative"),
    )
    op.create_index("ix_memory_narratives_moment_id", "memory_narratives", ["moment_id"])

    op.create_table(
        "memory_suppressions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("concept", sa.String(60), nullable=False),
        sa.Column("concept_key", sa.String(160), server_default="", nullable=False),
        sa.Column("reason", sa.String(30), server_default="forget", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "domain", "concept", "concept_key", name="uq_memory_suppression_identity"),
    )
    op.create_index("ix_memory_suppressions_user_id", "memory_suppressions", ["user_id"])
    op.create_index("ix_memory_suppressions_user", "memory_suppressions", ["user_id", "domain"])


def downgrade() -> None:
    op.drop_table("memory_suppressions")
    op.drop_table("memory_narratives")
    op.drop_table("memory_evidence")
    op.drop_table("memory_moments")
    op.drop_table("memory_belief_revisions")
    op.drop_table("memory_beliefs")
