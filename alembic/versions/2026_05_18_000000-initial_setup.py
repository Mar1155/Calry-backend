"""initial setup

Revision ID: initial_setup
Revises: None
Create Date: 2026-05-18 15:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "initial_setup"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create 'users' table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("firebase_uid", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("daily_calorie_goal", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column("goal_type", sa.String(length=50), nullable=False, server_default="maintain"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_firebase_uid"), "users", ["firebase_uid"], unique=True)

    # 2. Create 'meals' table
    op.create_table(
        "meals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("original_input", sa.Text(), nullable=False),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("estimated_calories", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed_calories", sa.Integer(), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meals_user_id"), "meals", ["user_id"], unique=False)

    # 3. Create 'meal_items' table
    op.create_table(
        "meal_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("meal_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("estimated_calories", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["meal_id"], ["meals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meal_items_meal_id"), "meal_items", ["meal_id"], unique=False)

    # 4. Create 'burned_calories' table
    op.create_table(
        "burned_calories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("activity_name", sa.String(length=255), nullable=True),
        sa.Column("calories", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_burned_calories_user_id"), "burned_calories", ["user_id"], unique=False)

    # 5. Create 'daily_summaries' table
    op.create_table(
        "daily_summaries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("consumed_calories", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("burned_calories", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("remaining_calories", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date", name="uq_user_date"),
    )
    op.create_index(op.f("ix_daily_summaries_date"), "daily_summaries", ["date"], unique=False)
    op.create_index(op.f("ix_daily_summaries_user_id"), "daily_summaries", ["user_id"], unique=False)

    # 6. Create 'ai_inference_logs' table
    op.create_table(
        "ai_inference_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("input_type", sa.String(length=50), nullable=False),
        sa.Column("raw_input", sa.Text(), nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ai_inference_logs_user_id"),
        "ai_inference_logs",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_inference_logs_user_id"), table_name="ai_inference_logs")
    op.drop_table("ai_inference_logs")
    op.drop_index(op.f("ix_daily_summaries_user_id"), table_name="daily_summaries")
    op.drop_index(op.f("ix_daily_summaries_date"), table_name="daily_summaries")
    op.drop_table("daily_summaries")
    op.drop_index(op.f("ix_burned_calories_user_id"), table_name="burned_calories")
    op.drop_table("burned_calories")
    op.drop_index(op.f("ix_meal_items_meal_id"), table_name="meal_items")
    op.drop_table("meal_items")
    op.drop_index(op.f("ix_meals_user_id"), table_name="meals")
    op.drop_table("meals")
    op.drop_index(op.f("ix_users_firebase_uid"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
