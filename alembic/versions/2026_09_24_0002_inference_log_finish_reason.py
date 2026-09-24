"""Add finish_reason to ai_inference_logs.

Revision ID: inference_finish_reason_0001
Revises: food_memory_snapshot_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "inference_finish_reason_0001"
down_revision: str | None = "food_memory_snapshot_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # C26: "length" flags a completion truncated by max_completion_tokens, so a
    # truncated meal estimate is directly queryable instead of only inferable
    # after the fact from a collapsed single-item result.
    op.add_column(
        "ai_inference_logs",
        sa.Column("finish_reason", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_inference_logs", "finish_reason")
