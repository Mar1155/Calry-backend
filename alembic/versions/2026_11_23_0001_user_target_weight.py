"""optional user goal weight

Revision ID: user_target_weight_0001
Revises: weight_entries_0001
Create Date: 2026-11-23 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "user_target_weight_0001"
down_revision: str | None = "weight_entries_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("target_weight_kg", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "target_weight_kg")
