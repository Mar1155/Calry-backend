"""add account owned onboarding state

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("onboarding_status", sa.String(32), nullable=False, server_default="not_started"))
    op.add_column("users", sa.Column("onboarding_step", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("onboarding_version", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("users", sa.Column("onboarding_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("preferred_unit_system", sa.String(16), nullable=False, server_default="metric"))
    op.add_column("users", sa.Column("activity_level", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("target_pace", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("calorie_target_source", sa.String(32), nullable=False, server_default="calculated"))
    # Existing active profiles stay out of a surprise onboarding loop.
    op.execute("UPDATE users SET onboarding_status = 'completed', onboarding_completed_at = CURRENT_TIMESTAMP WHERE sex IS NOT NULL AND age IS NOT NULL AND height_cm IS NOT NULL AND weight_kg IS NOT NULL")
    op.alter_column("users", "onboarding_status", server_default=None)
    op.alter_column("users", "onboarding_version", server_default=None)
    op.alter_column("users", "preferred_unit_system", server_default=None)
    op.alter_column("users", "calorie_target_source", server_default=None)


def downgrade() -> None:
    for column in ("calorie_target_source", "target_pace", "activity_level", "preferred_unit_system", "onboarding_completed_at", "onboarding_started_at", "onboarding_version", "onboarding_step", "onboarding_status"):
        op.drop_column("users", column)
