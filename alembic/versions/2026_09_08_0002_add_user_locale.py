"""Persist the user's app language for background AI generation.

Revision ID: user_locale_0002
Revises: a4b5c6d7e8f9
Create Date: 2026-09-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "user_locale_0002"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("locale", sa.String(length=12), nullable=False, server_default="en"),
    )


def downgrade() -> None:
    op.drop_column("users", "locale")
