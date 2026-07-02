"""add water glasses to daily summaries

Revision ID: e7f8a9b0c1d2
Revises: f1e2d3c4b5a6
Create Date: 2026-07-02 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "f1e2d3c4b5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_summaries",
        sa.Column("water_glasses", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("daily_summaries", "water_glasses")
