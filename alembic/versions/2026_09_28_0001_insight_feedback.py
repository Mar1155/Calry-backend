"""Persist optional insight usefulness feedback."""
import sqlalchemy as sa

from alembic import op

revision = "insight_feedback_0001"
down_revision = "scan_audit_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("proactive_insights", sa.Column("helpful", sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column("proactive_insights", "helpful")
