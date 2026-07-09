"""rename meal type and add category suggestion metadata

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-07-09 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e0f1a2b3c4d5"
down_revision: str | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("meals", "meal_type", new_column_name="meal_category")
    op.execute(
        """
        UPDATE meals
        SET meal_category = CASE
          WHEN EXTRACT(HOUR FROM created_at) BETWEEN 5 AND 10 THEN 'breakfast'
          WHEN EXTRACT(HOUR FROM created_at) BETWEEN 11 AND 15 THEN 'lunch'
          WHEN EXTRACT(HOUR FROM created_at) BETWEEN 16 AND 18 THEN 'snack'
          WHEN EXTRACT(HOUR FROM created_at) BETWEEN 19 AND 23 THEN 'dinner'
          ELSE 'snack'
        END
        """
    )
    op.add_column("meals", sa.Column("meal_category_suggestion", sa.String(length=20), nullable=True))
    op.add_column("meals", sa.Column("meal_category_confidence", sa.String(length=10), nullable=True))
    op.add_column("meal_analysis_jobs", sa.Column("meal_category", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("meal_analysis_jobs", "meal_category")
    op.drop_column("meals", "meal_category_confidence")
    op.drop_column("meals", "meal_category_suggestion")
    op.alter_column("meals", "meal_category", new_column_name="meal_type")
