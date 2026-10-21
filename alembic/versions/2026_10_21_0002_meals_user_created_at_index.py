"""Index meals by (user_id, created_at) for the meal timeline.

``MealRepository.get_by_user`` filters on ``user_id`` and orders by
``created_at DESC``; only ``(user_id)`` and ``(user_id, local_date)`` were
indexed, so Postgres sorted every user's meals per request.

Revision ID: meals_user_created_at_0001
Revises: client_timezone_days_0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "meals_user_created_at_0001"
down_revision: str | None = "client_timezone_days_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_meals_user_created_at", "meals", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_meals_user_created_at", table_name="meals")
