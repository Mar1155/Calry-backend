"""add_user_food_memory

Revision ID: a1b2c3d4e5f6
Revises: 8d2f170b0ccb
Create Date: 2026-06-18 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "8d2f170b0ccb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_food_memory",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("normalized_name", sa.String(500), nullable=False),
        sa.Column("display_name", sa.String(500), nullable=False),
        sa.Column("learned_calories", sa.Integer(), nullable=False),
        sa.Column("protein_g", sa.Float(), nullable=True),
        sa.Column("carbs_g", sa.Float(), nullable=True),
        sa.Column("fat_g", sa.Float(), nullable=True),
        sa.Column("items_snapshot", sa.JSON(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "normalized_name", name="uq_user_food_memory"),
    )
    op.create_index("ix_user_food_memory_id", "user_food_memory", ["id"])
    op.create_index("ix_user_food_memory_user_id", "user_food_memory", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_food_memory_user_id", table_name="user_food_memory")
    op.drop_index("ix_user_food_memory_id", table_name="user_food_memory")
    op.drop_table("user_food_memory")
