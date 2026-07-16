import datetime as dt
from typing import Any

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.meal import Meal, MealItem
from app.repositories.base import BaseRepository
from app.services.meal_invariants import InvalidMealIngredients, normalize_meal_ingredients


class MealRepository(BaseRepository[Meal]):
    """Repository handling all meal and nested meal item queries."""

    def __init__(self, db: AsyncSession):
        super().__init__(Meal, db)

    @staticmethod
    def _resolve_calories_per_100g(item_data: dict[str, Any]) -> float | None:
        calories_per_100g = item_data.get("calories_per_100g")
        if calories_per_100g is not None:
            return calories_per_100g

        weight_grams = item_data.get("weight_grams")
        estimated_calories = item_data.get("estimated_calories")
        if weight_grams and weight_grams > 0 and estimated_calories is not None:
            return round(estimated_calories / weight_grams * 100, 1)
        return None

    async def get_by_user(
        self, user_id: int, skip: int = 0, limit: int = 50, cutoff_date: dt.datetime | None = None
    ) -> list[Meal]:
        """Fetches a user's logged meals in descending order of creation (most recent first)."""
        stmt = select(Meal).where(Meal.user_id == user_id)
        if cutoff_date is not None:
            stmt = stmt.where(Meal.created_at >= cutoff_date)
        stmt = stmt.order_by(desc(Meal.created_at)).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_user_meals_on_date(self, user_id: int, date_val: dt.date) -> list[Meal]:
        """Gets all meals logged by a user on a specific calendar date.

        Used primarily for calculating dynamic daily summaries.
        """
        start_dt = dt.datetime.combine(date_val, dt.time.min).replace(tzinfo=dt.UTC)
        end_dt = dt.datetime.combine(date_val, dt.time.max).replace(tzinfo=dt.UTC)
        stmt = select(Meal).where(
            Meal.user_id == user_id,
            Meal.created_at >= start_dt,
            Meal.created_at <= end_dt,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update(self, db_obj: Meal, obj_in: dict[str, Any] | Any) -> Meal:
        """Update a meal while keeping ingredients as the calorie source of truth."""
        if isinstance(obj_in, dict):
            update_data = obj_in.copy()
        else:
            update_data = obj_in.model_dump(exclude_unset=True)

        previous_estimate = db_obj.estimated_calories
        is_confirmed = update_data.pop("is_confirmed", None)

        # 1. Replace nested ingredients only with a complete, valid set.
        if "items" in update_data:
            items_in = update_data.pop("items")
            if not items_in:
                raise InvalidMealIngredients("A meal must contain at least one ingredient.")
            normalized_items, _ = normalize_meal_ingredients(
                items_in,
                meal_name=update_data.get("meal_name") or db_obj.meal_name or db_obj.original_input,
                target_calories=None,
            )
            if not normalized_items:
                raise InvalidMealIngredients("A meal must contain at least one ingredient.")
            # Clear existing items
            for item in list(db_obj.items):
                await self.db.delete(item)
            db_obj.items.clear()
            await self.db.flush()

            # Add new items
            for item_data in normalized_items:
                self.db.add(
                    MealItem(
                        meal_id=db_obj.id,
                        name=item_data["name"],
                        quantity_estimate=item_data.get("quantity_estimate"),
                        weight_grams=item_data["weight_grams"],
                        calories_per_100g=item_data["calories_per_100g"],
                        protein_g=item_data.get("protein_g"),
                        carbs_g=item_data.get("carbs_g"),
                        fat_g=item_data.get("fat_g"),
                    )
                )
            await self.db.flush()

        # 3. Standard update loop for other attributes
        for field, value in update_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)

        self.db.add(db_obj)
        await self.db.flush()

        # Refresh relation to avoid stale session cache
        from sqlalchemy.future import select
        from sqlalchemy.orm import selectinload

        stmt = (
            select(Meal)
            .where(Meal.id == db_obj.id)
            .options(selectinload(Meal.items))
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        refreshed = result.scalar_one()
        if not refreshed.items:
            raise InvalidMealIngredients("A meal must contain at least one ingredient.")

        derived_total = sum(item.estimated_calories for item in refreshed.items)
        refreshed.estimated_calories = derived_total
        if refreshed.estimated_min_calories is not None:
            refreshed.estimated_min_calories = min(refreshed.estimated_min_calories, derived_total)
        if refreshed.estimated_max_calories is not None:
            refreshed.estimated_max_calories = max(refreshed.estimated_max_calories, derived_total)

        # Confirmation is a status, never an independently entered calorie value.
        if is_confirmed is True or refreshed.confirmed_calories is not None:
            refreshed.confirmed_calories = derived_total
            if is_confirmed is True:
                refreshed.confirmed_at = dt.datetime.now(dt.UTC)
            refreshed.correction_delta = derived_total - previous_estimate
            refreshed.correction_percent = (
                float((derived_total - previous_estimate) / previous_estimate * 100) if previous_estimate > 0 else 0.0
            )

        self.db.add(refreshed)
        await self.db.flush()
        return refreshed
