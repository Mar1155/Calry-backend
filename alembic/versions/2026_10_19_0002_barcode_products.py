"""Add barcode_products cache table for packaged food barcode scanning.

Revision ID: barcode_products_0001
Revises: user_ai_usage_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "barcode_products_0001"
down_revision: str | None = "user_ai_usage_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "barcode_products",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("product_name_i18n", sa.JSON(), nullable=True),
        sa.Column("brand", sa.String(255), nullable=True),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("calories_per_100g", sa.Float(), nullable=False),
        sa.Column("protein_per_100g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("carbs_per_100g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fat_per_100g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("serving_size_label", sa.String(100), nullable=True),
        sa.Column("serving_weight_grams", sa.Integer(), nullable=True),
        sa.Column("package_weight_grams", sa.Integer(), nullable=True),
        sa.Column("nutriscore_grade", sa.String(4), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="openfoodfacts"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(code)) >= 8", name="ck_barcode_product_code_min_len"),
        sa.CheckConstraint("length(trim(product_name)) > 0", name="ck_barcode_product_name_not_blank"),
        sa.CheckConstraint("calories_per_100g >= 0", name="ck_barcode_product_nonnegative_density"),
        sa.CheckConstraint("calories_per_100g <= 900", name="ck_barcode_product_density_upper_bound"),
        sa.UniqueConstraint("code", name="uq_barcode_products_code"),
    )
    op.create_index("ix_barcode_products_code", "barcode_products", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_barcode_products_code", table_name="barcode_products")
    op.drop_table("barcode_products")
