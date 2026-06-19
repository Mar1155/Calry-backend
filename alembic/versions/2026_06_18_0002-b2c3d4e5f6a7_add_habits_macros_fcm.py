"""add_habits_macros_fcm

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-18 00:02:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("daily_protein_goal", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("daily_carbs_goal", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("daily_fat_goal", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("fcm_token", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "fcm_token")
    op.drop_column("users", "daily_fat_goal")
    op.drop_column("users", "daily_carbs_goal")
    op.drop_column("users", "daily_protein_goal")
