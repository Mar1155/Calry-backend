"""add meal revisions

Revision ID: a6b7c8d9e0f1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-24 00:03:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meal_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("meal_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("refinement_type", sa.String(length=20), nullable=False),
        sa.Column("user_input", sa.Text(), nullable=False),
        sa.Column("previous_calories", sa.Integer(), nullable=False),
        sa.Column("revised_calories", sa.Integer(), nullable=False),
        sa.Column("calorie_delta", sa.Integer(), nullable=False),
        sa.Column("previous_items_json", sa.Text(), nullable=False),
        sa.Column("revised_items_json", sa.Text(), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["meal_id"], ["meals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meal_revisions_meal_id"), "meal_revisions", ["meal_id"], unique=False)
    op.create_index(op.f("ix_meal_revisions_user_id"), "meal_revisions", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_meal_revisions_user_id"), table_name="meal_revisions")
    op.drop_index(op.f("ix_meal_revisions_meal_id"), table_name="meal_revisions")
    op.drop_table("meal_revisions")
