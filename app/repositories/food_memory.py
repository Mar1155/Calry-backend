import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.food_memory import UserFoodMemory
from app.models.meal import Meal


class FoodMemoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_recents(self, user_id: int, limit: int = 10) -> list[UserFoodMemory]:
        stmt = (
            select(UserFoodMemory)
            .where(UserFoodMemory.user_id == user_id)
            .order_by(UserFoodMemory.last_used_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, memory_id: int, user_id: int) -> UserFoodMemory | None:
        stmt = select(UserFoodMemory).where(
            UserFoodMemory.id == memory_id,
            UserFoodMemory.user_id == user_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_from_meal(self, meal: Meal, confirmed_calories: int) -> UserFoodMemory:
        """Create or update food memory entry after user confirms a meal."""
        display_name = meal.meal_name or meal.original_input
        normalized = display_name.lower().strip()[:500]

        now = dt.datetime.now(dt.UTC)

        items_snapshot = None
        if meal.items:
            items_snapshot = [
                {
                    "name": item.name,
                    "estimated_calories": item.estimated_calories,
                    "quantity_estimate": item.quantity_estimate,
                    "weight_grams": item.weight_grams,
                    "calories_per_100g": item.calories_per_100g,
                    "protein_g": item.protein_g,
                    "carbs_g": item.carbs_g,
                    "fat_g": item.fat_g,
                }
                for item in meal.items
            ]

        existing_stmt = select(UserFoodMemory).where(
            UserFoodMemory.user_id == meal.user_id,
            UserFoodMemory.normalized_name == normalized,
        )
        result = await self.db.execute(existing_stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # Weighted average: (old * count + new) / (count + 1)
            new_count = existing.use_count + 1
            new_calories = round((existing.learned_calories * existing.use_count + confirmed_calories) / new_count)

            existing.learned_calories = new_calories
            existing.use_count = new_count
            existing.last_used_at = now
            existing.display_name = display_name
            if items_snapshot:
                existing.items_snapshot = items_snapshot
            if meal.total_protein_g is not None:
                existing.protein_g = meal.total_protein_g
            if meal.total_carbs_g is not None:
                existing.carbs_g = meal.total_carbs_g
            if meal.total_fat_g is not None:
                existing.fat_g = meal.total_fat_g
            await self.db.flush()
            return existing
        else:
            entry = UserFoodMemory(
                user_id=meal.user_id,
                normalized_name=normalized,
                display_name=display_name,
                learned_calories=confirmed_calories,
                protein_g=meal.total_protein_g,
                carbs_g=meal.total_carbs_g,
                fat_g=meal.total_fat_g,
                items_snapshot=items_snapshot,
                use_count=1,
                last_used_at=now,
                created_at=now,
            )
            self.db.add(entry)
            await self.db.flush()
            return entry
