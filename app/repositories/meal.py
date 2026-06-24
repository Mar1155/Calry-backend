import datetime as dt
from typing import Any

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.meal import Meal, MealItem
from app.repositories.base import BaseRepository


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
        """Updates meal details, handles nested items, and calculates correction delta/percent."""
        if isinstance(obj_in, dict):
            update_data = obj_in.copy()
        else:
            update_data = obj_in.model_dump(exclude_unset=True)

        # 1. Update nested food items if provided
        if "items" in update_data:
            items_in = update_data.pop("items")
            # Clear existing items
            for item in list(db_obj.items):
                await self.db.delete(item)
            db_obj.items.clear()
            await self.db.flush()

            # Add new items
            if items_in:
                for item_data in items_in:
                    if not isinstance(item_data, dict):
                        item_data = item_data.model_dump()
                    new_item = MealItem(
                        meal_id=db_obj.id,
                        name=item_data["name"],
                        quantity_estimate=item_data.get("quantity_estimate"),
                        weight_grams=item_data.get("weight_grams"),
                        calories_per_100g=self._resolve_calories_per_100g(item_data),
                        protein_g=item_data.get("protein_g"),
                        carbs_g=item_data.get("carbs_g"),
                        fat_g=item_data.get("fat_g"),
                    )
                    self.db.add(new_item)
                await self.db.flush()

        # 2. Update confirmed calories and calculate correction tracking metrics
        if "confirmed_calories" in update_data and update_data["confirmed_calories"] is not None:
            confirmed_cal = update_data.pop("confirmed_calories")
            db_obj.confirmed_calories = confirmed_cal
            db_obj.confirmed_at = dt.datetime.now(dt.UTC)
            db_obj.correction_delta = confirmed_cal - db_obj.estimated_calories
            if db_obj.estimated_calories > 0:
                db_obj.correction_percent = float(
                    (confirmed_cal - db_obj.estimated_calories) / db_obj.estimated_calories * 100
                )
            else:
                db_obj.correction_percent = 0.0

        # 3. Standard update loop for other attributes
        for field, value in update_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)

        self.db.add(db_obj)
        await self.db.flush()

        # Refresh relation to avoid stale session cache
        from sqlalchemy.future import select
        from sqlalchemy.orm import selectinload
        stmt = select(Meal).where(Meal.id == db_obj.id).options(selectinload(Meal.items))
        result = await self.db.execute(stmt)
        return result.scalar_one()
