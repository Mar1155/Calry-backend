"""add meal analysis jobs

Revision ID: c8d9e0f1a2b3
Revises: b3c4d5e6f7a8
Create Date: 2026-07-06 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meal_analysis_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("meal_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("image_url", sa.String(length=2048), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("additional_context", sa.Text(), nullable=True),
        sa.Column("locale", sa.String(length=12), nullable=True),
        sa.Column("client_request_id", sa.String(length=64), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["meal_id"], ["meals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_request_id", name="uq_meal_analysis_user_request"),
    )
    op.create_index(op.f("ix_meal_analysis_jobs_client_request_id"), "meal_analysis_jobs", ["client_request_id"], unique=False)
    op.create_index(op.f("ix_meal_analysis_jobs_meal_id"), "meal_analysis_jobs", ["meal_id"], unique=False)
    op.create_index(op.f("ix_meal_analysis_jobs_status"), "meal_analysis_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_meal_analysis_jobs_user_id"), "meal_analysis_jobs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_meal_analysis_jobs_user_id"), table_name="meal_analysis_jobs")
    op.drop_index(op.f("ix_meal_analysis_jobs_status"), table_name="meal_analysis_jobs")
    op.drop_index(op.f("ix_meal_analysis_jobs_meal_id"), table_name="meal_analysis_jobs")
    op.drop_index(op.f("ix_meal_analysis_jobs_client_request_id"), table_name="meal_analysis_jobs")
    op.drop_table("meal_analysis_jobs")
