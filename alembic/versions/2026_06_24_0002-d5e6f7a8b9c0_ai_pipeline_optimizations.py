"""ai_pipeline_optimizations

Adds columns supporting the AI pipeline redesign:
  - meals.confidence_score (deterministic confidence, C12)
  - meals.client_request_id + unique(user_id, client_request_id) (idempotency, C13)
  - user_food_memory.canonical_key + index (pre-inference cache, C3/C4)
  - ai_inference_logs.{prompt,completion,cached}_tokens (cost telemetry, C2)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-24 00:02:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # C12 — deterministic confidence score on meals.
    op.add_column("meals", sa.Column("confidence_score", sa.Float(), nullable=True))

    # C13 — idempotency key on meals.
    op.add_column("meals", sa.Column("client_request_id", sa.String(64), nullable=True))
    op.create_index("ix_meals_client_request_id", "meals", ["client_request_id"])
    op.create_unique_constraint("uq_meal_user_request", "meals", ["user_id", "client_request_id"])

    # C3/C4 — canonical cache key on food memory.
    op.add_column("user_food_memory", sa.Column("canonical_key", sa.String(500), nullable=True))
    op.create_index("ix_user_food_memory_canonical_key", "user_food_memory", ["canonical_key"])

    # C2 — token usage telemetry on inference logs.
    op.add_column("ai_inference_logs", sa.Column("prompt_tokens", sa.Integer(), nullable=True))
    op.add_column("ai_inference_logs", sa.Column("completion_tokens", sa.Integer(), nullable=True))
    op.add_column("ai_inference_logs", sa.Column("cached_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_inference_logs", "cached_tokens")
    op.drop_column("ai_inference_logs", "completion_tokens")
    op.drop_column("ai_inference_logs", "prompt_tokens")

    op.drop_index("ix_user_food_memory_canonical_key", table_name="user_food_memory")
    op.drop_column("user_food_memory", "canonical_key")

    op.drop_constraint("uq_meal_user_request", "meals", type_="unique")
    op.drop_index("ix_meals_client_request_id", table_name="meals")
    op.drop_column("meals", "client_request_id")
    op.drop_column("meals", "confidence_score")
