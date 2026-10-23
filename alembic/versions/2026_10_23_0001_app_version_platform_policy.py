"""per-platform app version policy and store urls

Revision ID: app_version_platform_0001
Revises: meals_user_created_at_0001
Create Date: 2026-10-23 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "app_version_platform_0001"
down_revision: str | None = "meals_user_created_at_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("min_supported_ios_version", sa.String(length=32), nullable=True))
    op.add_column("app_settings", sa.Column("min_supported_android_version", sa.String(length=32), nullable=True))
    op.add_column("app_settings", sa.Column("ios_store_url", sa.String(length=512), nullable=True))
    op.add_column("app_settings", sa.Column("android_store_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "android_store_url")
    op.drop_column("app_settings", "ios_store_url")
    op.drop_column("app_settings", "min_supported_android_version")
    op.drop_column("app_settings", "min_supported_ios_version")
