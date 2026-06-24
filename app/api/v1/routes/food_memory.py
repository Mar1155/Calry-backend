import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.meal import Meal, MealItem
from app.models.user import User
from app.repositories.food_memory import FoodMemoryRepository
from app.schemas.food_memory import FoodMemoryResponse
from app.schemas.meal import MealResponse
from app.services.summary import SummaryService

logger = logging.getLogger("app.api.food_memory")
router = APIRouter()


@router.get("/recents", response_model=list[FoodMemoryResponse])
async def get_recent_foods(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list:
    """Returns the user's most recently confirmed foods for one-tap repeat logging."""
    repo = FoodMemoryRepository(db)
    return await repo.get_recents(user_id=current_user.id, limit=10)


@router.post("/{memory_id}/log", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_from_memory(
    memory_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meal:
    """Instantly logs a meal from food memory — no AI call, uses learned calories."""
    repo = FoodMemoryRepository(db)
    memory = await repo.get_by_id(memory_id, current_user.id)

    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Food memory entry not found.",
        )

    now = dt.datetime.now(dt.UTC)

    meal = Meal(
        user_id=current_user.id,
        source_type="text",
        original_input=memory.display_name,
        meal_name=memory.display_name,
        estimated_calories=memory.learned_calories,
        confirmed_calories=memory.learned_calories,
        total_protein_g=memory.protein_g,
        total_carbs_g=memory.carbs_g,
        total_fat_g=memory.fat_g,
        ai_confidence="high",
        needs_clarification=False,
        confirmed_at=now,
    )
    db.add(meal)
    await db.flush()

    if memory.items_snapshot:
        for item_data in memory.items_snapshot:
            meal_item = MealItem(
                meal_id=meal.id,
                name=item_data["name"],
                quantity_estimate=item_data.get("quantity_estimate"),
                weight_grams=item_data.get("weight_grams"),
                calories_per_100g=item_data.get("calories_per_100g"),
                protein_g=item_data.get("protein_g"),
                carbs_g=item_data.get("carbs_g"),
                fat_g=item_data.get("fat_g"),
            )
            db.add(meal_item)

    await db.flush()

    # Update memory use_count and last_used_at
    memory.use_count += 1
    memory.last_used_at = now
    await db.flush()

    # Sync daily summary
    try:
        summary_service = SummaryService(db)
        await summary_service.sync_daily_summary(current_user.id, meal.created_at.date())
    except Exception as e:
        logger.error(f"Failed to sync daily summary on memory log: {e}")

    stmt = select(Meal).where(Meal.id == meal.id).options(selectinload(Meal.items))
    result = await db.execute(stmt)
    return result.scalar_one()
