"""store_item_calories_per_100g

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-06-24 00:01:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "meals" not in tables:
        op.create_table(
            "meals",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("source_type", sa.String(length=50), nullable=False),
            sa.Column("original_input", sa.Text(), nullable=False),
            sa.Column("image_url", sa.String(length=1024), nullable=True),
            sa.Column("audio_url", sa.String(length=1024), nullable=True),
            sa.Column("meal_name", sa.String(length=255), nullable=True),
            sa.Column("estimated_calories", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_min_calories", sa.Integer(), nullable=True),
            sa.Column("estimated_max_calories", sa.Integer(), nullable=True),
            sa.Column("total_protein_g", sa.Float(), nullable=True),
            sa.Column("total_carbs_g", sa.Float(), nullable=True),
            sa.Column("total_fat_g", sa.Float(), nullable=True),
            sa.Column("estimation_reasoning", sa.Text(), nullable=True),
            sa.Column("confirmed_calories", sa.Integer(), nullable=True),
            sa.Column("correction_delta", sa.Integer(), nullable=True),
            sa.Column("correction_percent", sa.Float(), nullable=True),
            sa.Column("ai_confidence", sa.String(length=50), nullable=True),
            sa.Column("needs_clarification", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("clarifying_question", sa.Text(), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_meals_user_id"), "meals", ["user_id"], unique=False)

    if "meal_items" not in tables:
        op.create_table(
            "meal_items",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("meal_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("quantity_estimate", sa.String(length=100), nullable=True),
            sa.Column("weight_grams", sa.Integer(), nullable=True),
            sa.Column("calories_per_100g", sa.Float(), nullable=True),
            sa.Column("protein_g", sa.Float(), nullable=True),
            sa.Column("carbs_g", sa.Float(), nullable=True),
            sa.Column("fat_g", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["meal_id"], ["meals.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_meal_items_meal_id"), "meal_items", ["meal_id"], unique=False)
        return

    columns = {column["name"] for column in inspector.get_columns("meal_items")}
    if "calories_per_100g" not in columns:
        op.add_column("meal_items", sa.Column("calories_per_100g", sa.Float(), nullable=True))
    if "estimated_calories" in columns:
        op.drop_column("meal_items", "estimated_calories")


def downgrade() -> None:
    op.add_column(
        "meal_items",
        sa.Column("estimated_calories", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_column("meal_items", "calories_per_100g")
