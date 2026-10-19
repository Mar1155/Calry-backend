"""Add user_ai_usage table (free-tier daily AI quota).

Revision ID: user_ai_usage_0001
Revises: ingredient_translations_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "user_ai_usage_0001"
down_revision: str | None = "ingredient_translations_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("request_key", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "request_key", name="uq_user_ai_usage_request"),
    )
    op.create_index("ix_user_ai_usage_user_id", "user_ai_usage", ["user_id"])
    op.create_index("ix_user_ai_usage_usage_date", "user_ai_usage", ["usage_date"])


def downgrade() -> None:
    op.drop_index("ix_user_ai_usage_usage_date", table_name="user_ai_usage")
    op.drop_index("ix_user_ai_usage_user_id", table_name="user_ai_usage")
    op.drop_table("user_ai_usage")
