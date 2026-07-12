"""add food memory favorites

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-07-11 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_food_memory",
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_user_food_memory_user_favorite_used",
        "user_food_memory",
        ["user_id", "is_favorite", "last_used_at"],
    )
    op.alter_column("user_food_memory", "is_favorite", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_user_food_memory_user_favorite_used", table_name="user_food_memory")
    op.drop_column("user_food_memory", "is_favorite")
