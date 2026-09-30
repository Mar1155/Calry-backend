"""Add ingredient_translations table (C29 cache-key normalization).

Revision ID: ingredient_translations_0001
Revises: ingredient_images_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ingredient_translations_0001"
down_revision: str | None = "ingredient_images_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingredient_translations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_key", sa.String(255), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("english_name", sa.String(255), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_key", name="uq_ingredient_translation_source_key"),
    )
    op.create_index("ix_ingredient_translations_source_key", "ingredient_translations", ["source_key"])


def downgrade() -> None:
    op.drop_index("ix_ingredient_translations_source_key", table_name="ingredient_translations")
    op.drop_table("ingredient_translations")
