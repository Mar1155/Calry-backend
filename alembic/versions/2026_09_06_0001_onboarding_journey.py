"""Persist onboarding offer and journey identity.

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""
import sqlalchemy as sa
from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("onboarding_offer_status", sa.String(16), server_default="handled", nullable=False))
    op.add_column("users", sa.Column("onboarding_journey_id", sa.String(32), nullable=True))
    op.create_index("ix_users_onboarding_journey_id", "users", ["onboarding_journey_id"], unique=True)
    op.create_table("onboarding_events",
        sa.Column("event_id", sa.String(32), primary_key=True),
        sa.Column("journey_id", sa.String(32), nullable=False),
        sa.Column("event_name", sa.String(32), nullable=False),
        sa.Column("step", sa.String(16), nullable=True),
        sa.Column("locale", sa.String(2), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_onboarding_events_journey_id", "onboarding_events", ["journey_id"])
    op.create_index("ix_onboarding_events_received_at", "onboarding_events", ["received_at"])


def downgrade():
    op.drop_table("onboarding_events")
    op.drop_index("ix_users_onboarding_journey_id", table_name="users")
    op.drop_column("users", "onboarding_journey_id")
    op.drop_column("users", "onboarding_offer_status")
