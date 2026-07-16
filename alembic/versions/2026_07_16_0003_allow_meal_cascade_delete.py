"""allow meal ingredient cascade deletion

Revision ID: e6f7a8b1c2d3
Revises: d5e6f7a8b1c2
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e6f7a8b1c2d3"
down_revision: str | None = "d5e6f7a8b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # SQLAlchemy can delete child rows before their parent. Mark the parent
    # deletion in this transaction so the deferred child constraint can tell
    # a cascade apart from an invalid attempt to leave a meal without items.
    op.execute("""
        CREATE OR REPLACE FUNCTION calry_mark_meal_deleting()
        RETURNS trigger AS $$
        BEGIN
            PERFORM set_config(
                'calry.deleting_meal_' || OLD.id::text,
                'on',
                true
            );
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_meals_mark_deleting ON meals")
    op.execute("""
        CREATE TRIGGER trg_meals_mark_deleting
        BEFORE DELETE ON meals
        FOR EACH ROW EXECUTE FUNCTION calry_mark_meal_deleting();
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION calry_require_meal_ingredient_for_item()
        RETURNS trigger AS $$
        DECLARE
            target_id integer;
            old_target_id integer;
            parent_is_deleting boolean;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                target_id := OLD.meal_id;
            ELSE
                target_id := NEW.meal_id;
            END IF;

            parent_is_deleting := COALESCE(
                current_setting(
                    'calry.deleting_meal_' || target_id::text,
                    true
                ) = 'on',
                false
            );

            IF NOT parent_is_deleting
               AND EXISTS (SELECT 1 FROM meals WHERE id = target_id)
               AND NOT EXISTS (SELECT 1 FROM meal_items WHERE meal_id = target_id) THEN
                RAISE EXCEPTION 'meal % must contain at least one ingredient', target_id;
            END IF;

            IF TG_OP = 'UPDATE' THEN
                old_target_id := OLD.meal_id;
                IF old_target_id <> NEW.meal_id
                   AND EXISTS (SELECT 1 FROM meals WHERE id = old_target_id)
                   AND NOT EXISTS (SELECT 1 FROM meal_items WHERE meal_id = old_target_id) THEN
                    RAISE EXCEPTION 'meal % must contain at least one ingredient', old_target_id;
                END IF;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP TRIGGER IF EXISTS trg_meals_mark_deleting ON meals")
    op.execute("DROP FUNCTION IF EXISTS calry_mark_meal_deleting()")
    op.execute("""
        CREATE OR REPLACE FUNCTION calry_require_meal_ingredient_for_item()
        RETURNS trigger AS $$
        DECLARE
            target_id integer;
            old_target_id integer;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                target_id := OLD.meal_id;
            ELSE
                target_id := NEW.meal_id;
            END IF;

            IF EXISTS (SELECT 1 FROM meals WHERE id = target_id)
               AND NOT EXISTS (SELECT 1 FROM meal_items WHERE meal_id = target_id) THEN
                RAISE EXCEPTION 'meal % must contain at least one ingredient', target_id;
            END IF;

            IF TG_OP = 'UPDATE' THEN
                old_target_id := OLD.meal_id;
                IF old_target_id <> NEW.meal_id
                   AND EXISTS (SELECT 1 FROM meals WHERE id = old_target_id)
                   AND NOT EXISTS (SELECT 1 FROM meal_items WHERE meal_id = old_target_id) THEN
                    RAISE EXCEPTION 'meal % must contain at least one ingredient', old_target_id;
                END IF;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
