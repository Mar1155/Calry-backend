"""Add detected_regions to meals (food region detection pins).

Revision ID: meal_detected_regions_0001
Revises: insight_feedback_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "meal_detected_regions_0001"
down_revision: str | None = "insight_feedback_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meals", sa.Column("detected_regions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("meals", "detected_regions")
