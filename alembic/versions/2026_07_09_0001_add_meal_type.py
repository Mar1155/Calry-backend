"""add meal type to meals

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-09 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d9e0f1a2b3c4"
down_revision: str | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meals",
        sa.Column("meal_type", sa.String(length=20), nullable=False, server_default="snack"),
    )
    op.alter_column("meals", "meal_type", server_default=None)


def downgrade() -> None:
    op.drop_column("meals", "meal_type")
