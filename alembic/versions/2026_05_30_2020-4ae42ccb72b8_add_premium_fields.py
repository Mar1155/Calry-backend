"""add_premium_fields

Revision ID: 4ae42ccb72b8
Revises: 353daa3ec5bc
Create Date: 2026-05-30 20:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4ae42ccb72b8'
down_revision: Union[str, None] = '353daa3ec5bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_premium", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("users", sa.Column("premium_entitlement", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("premium_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("revenuecat_app_user_id", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "updated_at")
    op.drop_column("users", "revenuecat_app_user_id")
    op.drop_column("users", "premium_expires_at")
    op.drop_column("users", "premium_entitlement")
    op.drop_column("users", "is_premium")
