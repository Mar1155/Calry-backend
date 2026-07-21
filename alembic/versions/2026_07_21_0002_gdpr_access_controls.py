"""add GDPR-oriented access controls and minimize admin records

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("access_status", sa.String(16), server_default="active", nullable=False))
    op.add_column("users", sa.Column("access_restriction_reason", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("access_restriction_legal_basis", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("access_restricted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("access_restriction_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("access_restricted_by_admin_uid", sa.String(255), nullable=True))
    op.create_index("ix_users_access_status", "users", ["access_status"])

    # Historical audit data predates pseudonymization. Remove unnecessary raw
    # emails/IPs rather than silently carrying them forward.
    op.execute(sa.text("UPDATE admin_audit_logs SET admin_email = NULL, safe_target_identifier = NULL, source_ip = NULL"))
    op.execute(sa.text("UPDATE user_deletion_jobs SET requested_by_admin_email = NULL"))
    op.execute(
        sa.text(
            "UPDATE user_deletion_jobs SET target_email = '[erased]', "
            "target_firebase_uid = 'erased:' || id, target_revenuecat_app_user_id = NULL, "
            "preview_snapshot_json = '{}' WHERE status = 'completed'"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_users_access_status", table_name="users")
    op.drop_column("users", "access_restricted_by_admin_uid")
    op.drop_column("users", "access_restriction_expires_at")
    op.drop_column("users", "access_restricted_at")
    op.drop_column("users", "access_restriction_legal_basis")
    op.drop_column("users", "access_restriction_reason")
    op.drop_column("users", "access_status")
