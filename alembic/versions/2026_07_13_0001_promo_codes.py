"""add RevenueCat-backed free promo codes

Revision ID: b3c4d5e6f7a9
Revises: a2b3c4d5e6f7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3c4d5e6f7a9"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("premium_last_verified_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("code_hint", sa.String(length=24), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="free_access"),
        sa.Column("offering_identifier", sa.String(length=255), nullable=True),
        sa.Column("grant_duration", sa.String(length=32), nullable=False, server_default="lifetime"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("redemption_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_redemptions IS NULL OR max_redemptions > 0", name="ck_promo_codes_max_redemptions"),
        sa.CheckConstraint("redemption_count >= 0", name="ck_promo_codes_redemption_count"),
        sa.CheckConstraint(
            "kind IN ('free_access', 'discounted_offering')",
            name="ck_promo_codes_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_promo_codes_code_digest", "promo_codes", ["code_digest"], unique=True)

    op.create_table(
        "promo_code_redemptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("promo_code_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("revenuecat_app_user_id", sa.String(length=255), nullable=False),
        sa.Column("grant_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("promo_code_id", "user_id", name="uq_promo_code_redemption_user"),
    )
    op.create_index("ix_promo_code_redemptions_promo_code_id", "promo_code_redemptions", ["promo_code_id"])
    op.create_index("ix_promo_code_redemptions_user_id", "promo_code_redemptions", ["user_id"])

    op.create_table(
        "promo_code_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_promo_code_attempts_user_id", "promo_code_attempts", ["user_id"])
    op.create_index("ix_promo_code_attempts_attempted_at", "promo_code_attempts", ["attempted_at"])


def downgrade() -> None:
    op.drop_index("ix_promo_code_attempts_attempted_at", table_name="promo_code_attempts")
    op.drop_index("ix_promo_code_attempts_user_id", table_name="promo_code_attempts")
    op.drop_table("promo_code_attempts")
    op.drop_index("ix_promo_code_redemptions_user_id", table_name="promo_code_redemptions")
    op.drop_index("ix_promo_code_redemptions_promo_code_id", table_name="promo_code_redemptions")
    op.drop_table("promo_code_redemptions")
    op.drop_index("ix_promo_codes_code_digest", table_name="promo_codes")
    op.drop_table("promo_codes")
    op.drop_column("users", "premium_last_verified_at")
