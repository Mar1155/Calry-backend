"""Shared factories for AI Memory System tests (not collected by pytest)."""

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.food_memory import UserFoodMemory
from app.models.meal import Meal, MealItem
from app.models.user import User

NOW = dt.datetime(2026, 7, 21, 12, 0, tzinfo=dt.UTC)


async def make_user(db: AsyncSession, *, uid: str = "mem_test_user", premium: bool = False) -> User:
    user = User(
        firebase_uid=uid,
        email=f"{uid}@example.com",
        name="Memory Tester",
        is_premium=premium,
        daily_calorie_goal=2000,
    )
    db.add(user)
    await db.flush()
    return user


async def make_meal(
    db: AsyncSession,
    user: User,
    *,
    name: str,
    grams: int,
    confirmed_calories: int = 500,
    correction_delta: int = 0,
    days_ago: int = 0,
    source_type: str = "text",
    category: str = "lunch",
    now: dt.datetime = NOW,
) -> Meal:
    observed = now - dt.timedelta(days=days_ago)
    estimated = confirmed_calories - correction_delta
    correction_percent = round(correction_delta / estimated * 100, 2) if estimated else 0.0
    meal = Meal(
        user_id=user.id,
        source_type=source_type,
        original_input=name,
        meal_name=name,
        meal_category=category,
        estimated_calories=estimated,
        confirmed_calories=confirmed_calories,
        correction_delta=correction_delta,
        correction_percent=correction_percent,
        confirmed_at=observed,
        created_at=observed,
        updated_at=observed,
    )
    db.add(meal)
    await db.flush()
    db.add(
        MealItem(
            meal_id=meal.id,
            name=name,
            weight_grams=grams,
            calories_per_100g=150.0,
            quantity_estimate=f"{grams}g",
        )
    )
    await db.flush()
    return meal


async def make_food_memory(
    db: AsyncSession,
    user: User,
    *,
    name: str,
    canonical_key: str,
    learned_calories: int = 400,
    use_count: int = 5,
    is_favorite: bool = False,
    now: dt.datetime = NOW,
) -> UserFoodMemory:
    fm = UserFoodMemory(
        user_id=user.id,
        normalized_name=name.lower(),
        canonical_key=canonical_key,
        display_name=name,
        learned_calories=learned_calories,
        use_count=use_count,
        is_favorite=is_favorite,
        last_used_at=now,
        created_at=now,
    )
    db.add(fm)
    await db.flush()
    return fm
