"""weight history and adaptive target review timestamp

Revision ID: weight_entries_0001
Revises: app_version_platform_0001
Create Date: 2026-11-20 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "weight_entries_0001"
down_revision: str | None = "app_version_platform_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weight_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="manual", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "local_date", name="uq_weight_entry_user_day"),
    )
    op.create_index("ix_weight_entries_user_id", "weight_entries", ["user_id"])
    op.add_column("users", sa.Column("adaptive_target_reviewed_at", sa.DateTime(timezone=True), nullable=True))

    # Seed each history with the single weight users already have, on the day
    # it was captured (onboarding completion, else account creation). UTC day:
    # a stored zone name Postgres does not know would abort the migration.
    op.execute(
        """
        INSERT INTO weight_entries (user_id, weight_kg, local_date, source, created_at, updated_at)
        SELECT
            u.id,
            u.weight_kg,
            (COALESCE(u.onboarding_completed_at, u.created_at) AT TIME ZONE 'UTC')::date,
            'onboarding',
            COALESCE(u.onboarding_completed_at, u.created_at),
            now()
        FROM users u
        WHERE u.weight_kg IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("users", "adaptive_target_reviewed_at")
    op.drop_index("ix_weight_entries_user_id", table_name="weight_entries")
    op.drop_table("weight_entries")
