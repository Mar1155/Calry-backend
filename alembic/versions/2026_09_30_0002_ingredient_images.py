"""Add ingredient_images table and meal_items.image_url (C29).

Revision ID: ingredient_images_0001
Revises: meal_detected_regions_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ingredient_images_0001"
down_revision: str | None = "meal_detected_regions_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meal_items", sa.Column("image_url", sa.String(1024), nullable=True))

    op.create_table(
        "ingredient_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("canonical_key", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("image_url", sa.String(1024), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("canonical_key", name="uq_ingredient_image_canonical_key"),
    )
    op.create_index("ix_ingredient_images_canonical_key", "ingredient_images", ["canonical_key"])


def downgrade() -> None:
    op.drop_index("ix_ingredient_images_canonical_key", table_name="ingredient_images")
    op.drop_table("ingredient_images")
    op.drop_column("meal_items", "image_url")
