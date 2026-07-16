"""split meal ingredient constraint triggers by table

Revision ID: d5e6f7a8b1c2
Revises: c4d5e6f7a8b0
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d5e6f7a8b1c2"
down_revision: str | None = "c4d5e6f7a8b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP TRIGGER IF EXISTS trg_meal_items_require_ingredient ON meal_items")
    op.execute("DROP TRIGGER IF EXISTS trg_meals_require_ingredient ON meals")
    op.execute("DROP FUNCTION IF EXISTS calry_require_meal_ingredient()")

    # PostgreSQL trigger records are table-specific. Keeping meals and
    # meal_items in separate functions prevents OLD/NEW from resolving fields
    # that do not exist on the current table (for example OLD.meal_id on meals).
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
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_meals_require_ingredient
        AFTER INSERT OR UPDATE ON meals
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION calry_require_meal_ingredient_for_meal();
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_meal_items_require_ingredient
        AFTER INSERT OR UPDATE OR DELETE ON meal_items
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION calry_require_meal_ingredient_for_item();
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP TRIGGER IF EXISTS trg_meal_items_require_ingredient ON meal_items")
    op.execute("DROP TRIGGER IF EXISTS trg_meals_require_ingredient ON meals")
    op.execute("DROP FUNCTION IF EXISTS calry_require_meal_ingredient_for_item()")
    op.execute("DROP FUNCTION IF EXISTS calry_require_meal_ingredient_for_meal()")
    op.execute("""
        CREATE OR REPLACE FUNCTION calry_require_meal_ingredient()
        RETURNS trigger AS $$
        DECLARE target_id integer;
        BEGIN
            target_id := CASE
                WHEN TG_TABLE_NAME = 'meals' THEN NEW.id
                WHEN TG_OP = 'DELETE' THEN OLD.meal_id
                ELSE NEW.meal_id
            END;
            IF EXISTS (SELECT 1 FROM meals WHERE id = target_id)
               AND NOT EXISTS (SELECT 1 FROM meal_items WHERE meal_id = target_id) THEN
                RAISE EXCEPTION 'meal % must contain at least one ingredient', target_id;
            END IF;
            IF TG_TABLE_NAME = 'meal_items' AND TG_OP = 'UPDATE' AND OLD.meal_id <> NEW.meal_id
               AND EXISTS (SELECT 1 FROM meals WHERE id = OLD.meal_id)
               AND NOT EXISTS (SELECT 1 FROM meal_items WHERE meal_id = OLD.meal_id) THEN
                RAISE EXCEPTION 'meal % must contain at least one ingredient', OLD.meal_id;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_meals_require_ingredient
        AFTER INSERT OR UPDATE ON meals
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION calry_require_meal_ingredient();
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_meal_items_require_ingredient
        AFTER INSERT OR UPDATE OR DELETE ON meal_items
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION calry_require_meal_ingredient();
    """)
