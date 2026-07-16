"""ignore ingredient invariant checks for deleted meals

Revision ID: f7a8b1c2d3e4
Revises: e6f7a8b1c2d3
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7a8b1c2d3e4"
down_revision: str | None = "e6f7a8b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Deleting the last item synchronizes the meal total to zero before the ORM
    # deletes the parent. That UPDATE queues this deferred trigger. By the time
    # it runs at commit the parent may legitimately be gone, so only enforce the
    # invariant for meals that still exist.
    op.execute("""
        CREATE OR REPLACE FUNCTION calry_require_meal_ingredient_for_meal()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM meals WHERE id = NEW.id)
               AND NOT EXISTS (SELECT 1 FROM meal_items WHERE meal_id = NEW.id) THEN
                RAISE EXCEPTION 'meal % must contain at least one ingredient', NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("""
        CREATE OR REPLACE FUNCTION calry_require_meal_ingredient_for_meal()
        RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM meal_items WHERE meal_id = NEW.id) THEN
                RAISE EXCEPTION 'meal % must contain at least one ingredient', NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
