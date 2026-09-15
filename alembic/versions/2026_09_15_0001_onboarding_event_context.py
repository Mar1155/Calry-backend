"""Add release and session context to onboarding events.

Revision ID: onboarding_context_0001
Revises: user_locale_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "onboarding_context_0001"
down_revision: str | None = "user_locale_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("onboarding_events", sa.Column("session_id", sa.String(32), nullable=True))
    op.add_column("onboarding_events", sa.Column("onboarding_version", sa.Integer(), nullable=True))
    op.add_column("onboarding_events", sa.Column("variant", sa.String(32), nullable=True))
    op.add_column("onboarding_events", sa.Column("app_version", sa.String(32), nullable=True))
    op.add_column("onboarding_events", sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("onboarding_events", "duration_ms")
    op.drop_column("onboarding_events", "app_version")
    op.drop_column("onboarding_events", "variant")
    op.drop_column("onboarding_events", "onboarding_version")
    op.drop_column("onboarding_events", "session_id")
