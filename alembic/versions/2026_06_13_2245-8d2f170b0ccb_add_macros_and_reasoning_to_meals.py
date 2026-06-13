"""add_macros_and_reasoning_to_meals

Revision ID: 8d2f170b0ccb
Revises: 4ae42ccb72b8
Create Date: 2026-06-13 22:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d2f170b0ccb'
down_revision: Union[str, None] = '4ae42ccb72b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns to meals table
    op.add_column("meals", sa.Column("total_protein_g", sa.Float(), nullable=True))
    op.add_column("meals", sa.Column("total_carbs_g", sa.Float(), nullable=True))
    op.add_column("meals", sa.Column("total_fat_g", sa.Float(), nullable=True))
    op.add_column("meals", sa.Column("estimation_reasoning", sa.Text(), nullable=True))

    # Add columns to meal_items table
    op.add_column("meal_items", sa.Column("weight_grams", sa.Integer(), nullable=True))
    op.add_column("meal_items", sa.Column("protein_g", sa.Float(), nullable=True))
    op.add_column("meal_items", sa.Column("carbs_g", sa.Float(), nullable=True))
    op.add_column("meal_items", sa.Column("fat_g", sa.Float(), nullable=True))


def downgrade() -> None:
    # Drop columns from meal_items table
    op.drop_column("meal_items", "fat_g")
    op.drop_column("meal_items", "carbs_g")
    op.drop_column("meal_items", "protein_g")
    op.drop_column("meal_items", "weight_grams")

    # Drop columns from meals table
    op.drop_column("meals", "estimation_reasoning")
    op.drop_column("meals", "total_fat_g")
    op.drop_column("meals", "total_carbs_g")
    op.drop_column("meals", "total_protein_g")
