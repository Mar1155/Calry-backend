"""enforce ingredient-backed meal calories

Revision ID: c4d5e6f7a8b0
Revises: b3c4d5e6f7a9
"""

import math
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d5e6f7a8b0"
down_revision: str | None = "b3c4d5e6f7a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GRAMS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*g\b", re.IGNORECASE)


def _weight(quantity: str | None) -> int:
    match = _GRAMS_RE.search(quantity or "")
    if match:
        return max(1, int(round(float(match.group(1).replace(",", ".")))))
    return 100


def _backfill_historical_meals(bind: sa.Connection) -> None:
    meals = list(
        bind.execute(
            sa.text(
                "SELECT id, meal_name, original_input, estimated_calories, confirmed_calories " "FROM meals ORDER BY id"
            )
        ).mappings()
    )

    for meal in meals:
        items = list(
            bind.execute(
                sa.text(
                    "SELECT id, name, quantity_estimate, weight_grams, calories_per_100g "
                    "FROM meal_items WHERE meal_id = :meal_id ORDER BY id"
                ),
                {"meal_id": meal["id"]},
            ).mappings()
        )
        target = meal["confirmed_calories"]
        if target is None:
            target = meal["estimated_calories"] or 0
        target = max(0, int(target))
        fallback_name = (meal["meal_name"] or meal["original_input"] or "Meal ingredient").strip()

        if not items:
            weight = max(100, int(math.ceil(target * 100 / 900))) if target else 100
            bind.execute(
                sa.text(
                    "INSERT INTO meal_items "
                    "(meal_id, name, quantity_estimate, weight_grams, calories_per_100g, created_at) "
                    "VALUES (:meal_id, :name, :quantity, :weight, :density, CURRENT_TIMESTAMP)"
                ),
                {
                    "meal_id": meal["id"],
                    "name": fallback_name or "Meal ingredient",
                    "quantity": f"{weight} g",
                    "weight": weight,
                    "density": target * 100 / weight,
                },
            )
            items = list(
                bind.execute(
                    sa.text(
                        "SELECT id, name, quantity_estimate, weight_grams, calories_per_100g "
                        "FROM meal_items WHERE meal_id = :meal_id ORDER BY id"
                    ),
                    {"meal_id": meal["id"]},
                ).mappings()
            )

        resolved_total = 0
        unresolved: list[tuple[int, int]] = []
        for item in items:
            name = (item["name"] or fallback_name or "Meal ingredient").strip() or "Meal ingredient"
            original_weight = item["weight_grams"]
            weight = original_weight if original_weight and original_weight > 0 else _weight(item["quantity_estimate"])
            density = item["calories_per_100g"]
            if density is not None and math.isfinite(float(density)) and density >= 0:
                item_calories = max(0, int(math.floor(weight * float(density) / 100 + 0.5)))
                if density > 900:
                    weight = max(weight, int(math.ceil(item_calories * 100 / 900)))
                    density = item_calories * 100 / weight
                resolved_total += item_calories
            else:
                unresolved.append((item["id"], weight))
            quantity = item["quantity_estimate"]
            if not quantity or weight != original_weight:
                quantity = f"{weight} g"
            bind.execute(
                sa.text(
                    "UPDATE meal_items SET name = :name, weight_grams = :weight, "
                    "calories_per_100g = :density, "
                    "quantity_estimate = :quantity WHERE id = :id"
                ),
                {
                    "id": item["id"],
                    "name": name,
                    "weight": weight,
                    "density": density,
                    "quantity": quantity,
                },
            )

        remaining = max(0, target - resolved_total)
        for index, (item_id, weight) in enumerate(unresolved):
            slots = len(unresolved) - index
            allocated = remaining if slots == 1 else int(round(remaining / slots))
            remaining -= allocated
            repaired_weight = max(weight, int(math.ceil(allocated * 100 / 900))) if allocated else weight
            bind.execute(
                sa.text(
                    "UPDATE meal_items SET weight_grams = :weight, calories_per_100g = :density, "
                    "quantity_estimate = CASE WHEN :weight <> :old_weight THEN :quantity "
                    "ELSE quantity_estimate END WHERE id = :id"
                ),
                {
                    "id": item_id,
                    "weight": repaired_weight,
                    "old_weight": weight,
                    "density": allocated * 100 / repaired_weight,
                    "quantity": f"{repaired_weight} g",
                },
            )

    # The database's rounding semantics define the persisted derived value.
    bind.execute(
        sa.text(
            "UPDATE meals SET estimated_calories = COALESCE(("
            "SELECT SUM(CAST(FLOOR(mi.weight_grams * mi.calories_per_100g / 100.0 + 0.5) AS INTEGER)) "
            "FROM meal_items mi WHERE mi.meal_id = meals.id), 0), "
            "confirmed_calories = CASE WHEN confirmed_calories IS NULL THEN NULL ELSE COALESCE(("
            "SELECT SUM(CAST(FLOOR(mi.weight_grams * mi.calories_per_100g / 100.0 + 0.5) AS INTEGER)) "
            "FROM meal_items mi WHERE mi.meal_id = meals.id), 0) END"
        )
    )


def _create_postgres_guards() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION calry_sync_meal_calories_from_items()
        RETURNS trigger AS $$
        DECLARE
            target_id integer;
            derived_total integer;
        BEGIN
            target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.meal_id ELSE NEW.meal_id END;
            SELECT COALESCE(SUM(FLOOR(weight_grams * calories_per_100g / 100.0 + 0.5)), 0)::integer
              INTO derived_total FROM meal_items WHERE meal_id = target_id;
            UPDATE meals
               SET estimated_calories = derived_total,
                   confirmed_calories = CASE
                       WHEN confirmed_calories IS NULL THEN NULL ELSE derived_total
                   END
             WHERE id = target_id;

            IF TG_OP = 'UPDATE' AND OLD.meal_id <> NEW.meal_id THEN
                SELECT COALESCE(SUM(FLOOR(weight_grams * calories_per_100g / 100.0 + 0.5)), 0)::integer
                  INTO derived_total FROM meal_items WHERE meal_id = OLD.meal_id;
                UPDATE meals
                   SET estimated_calories = derived_total,
                       confirmed_calories = CASE
                           WHEN confirmed_calories IS NULL THEN NULL ELSE derived_total
                       END
                 WHERE id = OLD.meal_id;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_meal_items_sync_calories
        AFTER INSERT OR UPDATE OR DELETE ON meal_items
        FOR EACH ROW EXECUTE FUNCTION calry_sync_meal_calories_from_items();
        """)
    op.execute("""
        CREATE OR REPLACE FUNCTION calry_derive_meal_calories()
        RETURNS trigger AS $$
        DECLARE derived_total integer;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                derived_total := 0;
            ELSE
                SELECT COALESCE(SUM(FLOOR(weight_grams * calories_per_100g / 100.0 + 0.5)), 0)::integer
                  INTO derived_total FROM meal_items WHERE meal_id = NEW.id;
            END IF;
            NEW.estimated_calories := derived_total;
            IF NEW.confirmed_calories IS NOT NULL THEN
                NEW.confirmed_calories := derived_total;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_meals_derive_calories
        BEFORE INSERT OR UPDATE OF estimated_calories, confirmed_calories ON meals
        FOR EACH ROW EXECUTE FUNCTION calry_derive_meal_calories();
        """)
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

        CREATE CONSTRAINT TRIGGER trg_meals_require_ingredient
        AFTER INSERT OR UPDATE ON meals
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION calry_require_meal_ingredient();

        CREATE CONSTRAINT TRIGGER trg_meal_items_require_ingredient
        AFTER INSERT OR UPDATE OR DELETE ON meal_items
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION calry_require_meal_ingredient();
        """)


def upgrade() -> None:
    bind = op.get_bind()
    _backfill_historical_meals(bind)

    op.alter_column("meal_items", "weight_grams", existing_type=sa.Integer(), nullable=False)
    op.alter_column("meal_items", "calories_per_100g", existing_type=sa.Float(), nullable=False)
    op.create_check_constraint("ck_meal_item_name_not_blank", "meal_items", "length(trim(name)) > 0")
    op.create_check_constraint("ck_meal_item_positive_weight", "meal_items", "weight_grams > 0")
    op.create_check_constraint("ck_meal_item_weight_upper_bound", "meal_items", "weight_grams <= 2147483647")
    op.create_check_constraint(
        "ck_meal_item_nonnegative_density",
        "meal_items",
        "calories_per_100g >= 0",
    )
    op.create_check_constraint("ck_meal_item_density_upper_bound", "meal_items", "calories_per_100g <= 900")

    if bind.dialect.name == "postgresql":
        op.execute("""
            UPDATE daily_summaries AS ds
               SET consumed_calories = (
                       SELECT COALESCE(SUM(m.estimated_calories), 0)::integer
                         FROM meals m
                        WHERE m.user_id = ds.user_id AND CAST(m.created_at AS date) = ds.date
                   ),
                   remaining_calories = users.daily_calorie_goal - (
                       SELECT COALESCE(SUM(m.estimated_calories), 0)::integer
                         FROM meals m
                        WHERE m.user_id = ds.user_id AND CAST(m.created_at AS date) = ds.date
                   ) + ds.burned_calories
              FROM users
             WHERE users.id = ds.user_id
            """)
        _create_postgres_guards()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_meal_items_require_ingredient ON meal_items")
        op.execute("DROP TRIGGER IF EXISTS trg_meals_require_ingredient ON meals")
        op.execute("DROP TRIGGER IF EXISTS trg_meals_derive_calories ON meals")
        op.execute("DROP TRIGGER IF EXISTS trg_meal_items_sync_calories ON meal_items")
        op.execute("DROP FUNCTION IF EXISTS calry_require_meal_ingredient()")
        op.execute("DROP FUNCTION IF EXISTS calry_derive_meal_calories()")
        op.execute("DROP FUNCTION IF EXISTS calry_sync_meal_calories_from_items()")

    op.drop_constraint("ck_meal_item_density_upper_bound", "meal_items", type_="check")
    op.drop_constraint("ck_meal_item_nonnegative_density", "meal_items", type_="check")
    op.drop_constraint("ck_meal_item_weight_upper_bound", "meal_items", type_="check")
    op.drop_constraint("ck_meal_item_positive_weight", "meal_items", type_="check")
    op.drop_constraint("ck_meal_item_name_not_blank", "meal_items", type_="check")
    op.alter_column("meal_items", "calories_per_100g", existing_type=sa.Float(), nullable=True)
    op.alter_column("meal_items", "weight_grams", existing_type=sa.Integer(), nullable=True)
