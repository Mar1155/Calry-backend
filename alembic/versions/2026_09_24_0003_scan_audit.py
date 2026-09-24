"""Scan audit: link inference logs to meals, capture quality signals, add reviews.

Revision ID: scan_audit_0001
Revises: inference_finish_reason_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "scan_audit_0001"
down_revision: str | None = "inference_finish_reason_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_inference_logs", sa.Column("meal_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_ai_inference_logs_meal_id",
        "ai_inference_logs",
        "meals",
        ["meal_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_ai_inference_logs_meal_id", "ai_inference_logs", ["meal_id"])
    op.create_index("ix_ai_inference_logs_created_at", "ai_inference_logs", ["created_at"])
    op.add_column("ai_inference_logs", sa.Column("item_count", sa.Integer(), nullable=True))
    op.add_column("ai_inference_logs", sa.Column("estimated_calories", sa.Integer(), nullable=True))
    op.add_column("ai_inference_logs", sa.Column("confidence_score", sa.Float(), nullable=True))
    op.add_column("ai_inference_logs", sa.Column("degraded_extraction", sa.Boolean(), nullable=True))
    op.add_column("ai_inference_logs", sa.Column("needs_clarification", sa.Boolean(), nullable=True))
    op.add_column("ai_inference_logs", sa.Column("quality_json", sa.JSON(), nullable=True))

    op.create_table(
        "scan_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "inference_log_id",
            sa.Integer(),
            sa.ForeignKey("ai_inference_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(40), nullable=False),
        sa.Column("expected_calories", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_by_admin_uid", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("inference_log_id", name="uq_scan_review_log"),
    )
    op.create_index("ix_scan_reviews_inference_log_id", "scan_reviews", ["inference_log_id"])
    op.create_index("ix_scan_reviews_user_id", "scan_reviews", ["user_id"])
    op.create_index("ix_scan_reviews_verdict", "scan_reviews", ["verdict"])


def downgrade() -> None:
    op.drop_index("ix_scan_reviews_verdict", table_name="scan_reviews")
    op.drop_index("ix_scan_reviews_user_id", table_name="scan_reviews")
    op.drop_index("ix_scan_reviews_inference_log_id", table_name="scan_reviews")
    op.drop_table("scan_reviews")
    for column in (
        "quality_json",
        "needs_clarification",
        "degraded_extraction",
        "confidence_score",
        "estimated_calories",
        "item_count",
    ):
        op.drop_column("ai_inference_logs", column)
    op.drop_index("ix_ai_inference_logs_created_at", table_name="ai_inference_logs")
    op.drop_index("ix_ai_inference_logs_meal_id", table_name="ai_inference_logs")
    op.drop_constraint("fk_ai_inference_logs_meal_id", "ai_inference_logs", type_="foreignkey")
    op.drop_column("ai_inference_logs", "meal_id")
