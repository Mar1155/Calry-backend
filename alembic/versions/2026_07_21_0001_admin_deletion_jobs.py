"""add secure admin deletion jobs and audit log

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: str | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("deletion_in_progress", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "user_deletion_jobs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("target_user_id", sa.Integer(), nullable=False),
        sa.Column("target_email", sa.String(255), nullable=False),
        sa.Column("target_firebase_uid", sa.String(255), nullable=False),
        sa.Column("target_revenuecat_app_user_id", sa.String(255), nullable=True),
        sa.Column("requested_by_admin_uid", sa.String(255), nullable=False),
        sa.Column("requested_by_admin_email", sa.String(255), nullable=True),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_step", sa.String(64), nullable=True),
        sa.Column("preview_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_user_deletion_jobs_target_user_id", "user_deletion_jobs", ["target_user_id"])
    op.create_index("ix_user_deletion_jobs_target_firebase_uid", "user_deletion_jobs", ["target_firebase_uid"])
    op.create_index("ix_user_deletion_jobs_requested_by_admin_uid", "user_deletion_jobs", ["requested_by_admin_uid"])
    op.create_index("ix_user_deletion_jobs_idempotency_key", "user_deletion_jobs", ["idempotency_key"], unique=True)
    op.create_index("ix_user_deletion_jobs_status", "user_deletion_jobs", ["status"])
    op.create_index("ix_user_deletion_jobs_target_status", "user_deletion_jobs", ["target_user_id", "status"])
    op.create_index(
        "uq_user_deletion_jobs_one_active",
        "user_deletion_jobs",
        ["target_user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running', 'partially_failed')"),
        sqlite_where=sa.text("status IN ('pending', 'running', 'partially_failed')"),
    )

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admin_uid", sa.String(255), nullable=False),
        sa.Column("admin_email", sa.String(255), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("safe_target_identifier", sa.String(255), nullable=True),
        sa.Column("deletion_job_id", sa.String(40), nullable=True),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("source_ip", sa.String(64), nullable=True),
    )
    for column in ("timestamp", "admin_uid", "action", "target_user_id", "deletion_job_id", "request_id"):
        op.create_index(f"ix_admin_audit_logs_{column}", "admin_audit_logs", [column])


def downgrade() -> None:
    op.drop_table("admin_audit_logs")
    op.drop_table("user_deletion_jobs")
    op.drop_column("users", "deletion_in_progress")
