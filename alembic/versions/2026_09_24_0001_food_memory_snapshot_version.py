"""Add items_snapshot_version to user_food_memory.

Revision ID: food_memory_snapshot_0001
Revises: onboarding_context_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "food_memory_snapshot_0001"
down_revision: str | None = "onboarding_context_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL means "legacy snapshot" (captured before ingredient decomposition,
    # C20/C21) — existing rows get NULL for free and are treated as stale by the
    # cache lookup until the user reconfirms that meal.
    op.add_column(
        "user_food_memory",
        sa.Column("items_snapshot_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_food_memory", "items_snapshot_version")
