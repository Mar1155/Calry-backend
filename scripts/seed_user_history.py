"""Seed realistic nutrition history for a Firebase user.

Usage:
    python scripts/seed_user_history.py <firebase_uid>
    python scripts/seed_user_history.py <firebase_uid> --days 10 --append
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.burned_calories import BurnedCalories  # noqa: E402
from app.models.daily_summary import DailySummary  # noqa: E402
from app.models.meal import Meal, MealItem  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.summary import SummaryService  # noqa: E402


@dataclass(frozen=True)
class FoodItem:
    name: str
    calories: int


@dataclass(frozen=True)
class MealTemplate:
    source_type: str
    original_input: str
    time: dt.time
    items: tuple[FoodItem, ...]

    @property
    def calories(self) -> int:
        return sum(item.calories for item in self.items)


@dataclass(frozen=True)
class ActivityTemplate:
    name: str
    calories: int
    time: dt.time


BREAKFASTS: tuple[MealTemplate, ...] = (
    MealTemplate(
        "text",
        "Greek yogurt with berries, granola and a cappuccino",
        dt.time(7, 45),
        (
            FoodItem("Greek yogurt", 150),
            FoodItem("Mixed berries", 55),
            FoodItem("Granola", 170),
            FoodItem("Cappuccino", 85),
        ),
    ),
    MealTemplate(
        "text",
        "Two scrambled eggs, whole grain toast and orange juice",
        dt.time(8, 10),
        (
            FoodItem("Scrambled eggs (2)", 210),
            FoodItem("Whole grain toast", 135),
            FoodItem("Butter", 70),
            FoodItem("Orange juice", 110),
        ),
    ),
    MealTemplate(
        "text",
        "Oatmeal with banana, peanut butter and espresso",
        dt.time(7, 30),
        (
            FoodItem("Oatmeal", 190),
            FoodItem("Banana", 105),
            FoodItem("Peanut butter", 95),
            FoodItem("Espresso", 5),
        ),
    ),
)

LUNCHES: tuple[MealTemplate, ...] = (
    MealTemplate(
        "text",
        "Chicken rice bowl with vegetables and olive oil dressing",
        dt.time(12, 50),
        (
            FoodItem("Grilled chicken breast", 260),
            FoodItem("Steamed rice", 240),
            FoodItem("Roasted vegetables", 120),
            FoodItem("Olive oil dressing", 90),
        ),
    ),
    MealTemplate(
        "text",
        "Pasta with tomato sauce, parmesan and side salad",
        dt.time(13, 15),
        (
            FoodItem("Pasta", 390),
            FoodItem("Tomato sauce", 80),
            FoodItem("Parmesan", 55),
            FoodItem("Side salad", 95),
        ),
    ),
    MealTemplate(
        "text",
        "Turkey sandwich with avocado, apple and sparkling water",
        dt.time(12, 35),
        (
            FoodItem("Whole grain bread", 210),
            FoodItem("Turkey breast", 140),
            FoodItem("Avocado", 120),
            FoodItem("Apple", 80),
        ),
    ),
)

DINNERS: tuple[MealTemplate, ...] = (
    MealTemplate(
        "text",
        "Salmon with potatoes and green beans",
        dt.time(19, 45),
        (
            FoodItem("Baked salmon", 330),
            FoodItem("Roasted potatoes", 260),
            FoodItem("Green beans", 60),
            FoodItem("Olive oil", 80),
        ),
    ),
    MealTemplate(
        "text",
        "Beef stir fry with noodles and vegetables",
        dt.time(20, 10),
        (
            FoodItem("Lean beef", 280),
            FoodItem("Noodles", 310),
            FoodItem("Stir fry vegetables", 120),
            FoodItem("Soy sesame sauce", 75),
        ),
    ),
    MealTemplate(
        "text",
        "Vegetable soup, grilled cheese and mixed salad",
        dt.time(19, 30),
        (
            FoodItem("Vegetable soup", 180),
            FoodItem("Grilled cheese sandwich", 420),
            FoodItem("Mixed salad", 90),
        ),
    ),
)

SNACKS: tuple[MealTemplate, ...] = (
    MealTemplate(
        "text",
        "Protein bar and black coffee",
        dt.time(16, 20),
        (FoodItem("Protein bar", 210), FoodItem("Black coffee", 5)),
    ),
    MealTemplate(
        "text",
        "Banana with almonds",
        dt.time(17, 10),
        (FoodItem("Banana", 105), FoodItem("Almonds", 170)),
    ),
    MealTemplate(
        "text",
        "Cottage cheese with honey",
        dt.time(16, 45),
        (FoodItem("Cottage cheese", 160), FoodItem("Honey", 45)),
    ),
)

ACTIVITIES: tuple[ActivityTemplate, ...] = (
    ActivityTemplate("Morning walk", 170, dt.time(7, 5)),
    ActivityTemplate("Strength training", 310, dt.time(18, 20)),
    ActivityTemplate("Cycling commute", 240, dt.time(18, 45)),
    ActivityTemplate("Easy run", 390, dt.time(19, 5)),
    ActivityTemplate("Yoga class", 150, dt.time(18, 30)),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed realistic meals, burned calories and daily summaries for a Firebase user."
    )
    parser.add_argument("firebase_uid", help="Firebase UID of an existing user account.")
    parser.add_argument("--days", type=int, default=10, help="Number of calendar days to seed, including today.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append new rows instead of replacing existing rows in the seeded date range.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed. Defaults to a stable seed derived from the Firebase UID.",
    )
    return parser.parse_args()


def jitter_time(base_time: dt.time, rng: random.Random) -> dt.time:
    base_minutes = base_time.hour * 60 + base_time.minute
    minutes = max(0, min(23 * 60 + 59, base_minutes + rng.randint(-18, 22)))
    return dt.time(minutes // 60, minutes % 60)


def timestamp_for(day: dt.date, base_time: dt.time, rng: random.Random) -> dt.datetime:
    return dt.datetime.combine(day, jitter_time(base_time, rng), tzinfo=dt.UTC)


def vary_calories(calories: int, rng: random.Random, spread: float = 0.08) -> int:
    multiplier = 1 + rng.uniform(-spread, spread)
    return max(1, round(calories * multiplier))


def build_meal(user_id: int, day: dt.date, template: MealTemplate, rng: random.Random) -> Meal:
    items = [MealItem(name=item.name, estimated_calories=vary_calories(item.calories, rng)) for item in template.items]
    total = sum(item.estimated_calories for item in items)
    confirmed_calories = total if rng.random() < 0.35 else None
    meal = Meal(
        user_id=user_id,
        source_type=template.source_type,
        original_input=template.original_input,
        estimated_calories=total,
        confirmed_calories=confirmed_calories,
        ai_confidence=round(rng.uniform(0.78, 0.94), 2),
        created_at=timestamp_for(day, template.time, rng),
    )
    meal.items = items
    return meal


def build_activity(user_id: int, day: dt.date, template: ActivityTemplate, rng: random.Random) -> BurnedCalories:
    return BurnedCalories(
        user_id=user_id,
        activity_name=template.name,
        calories=vary_calories(template.calories, rng, spread=0.12),
        created_at=timestamp_for(day, template.time, rng),
    )


async def get_user(session: AsyncSession, firebase_uid: str) -> User:
    result = await session.execute(select(User).where(User.firebase_uid == firebase_uid))
    user = result.scalar_one_or_none()
    if user is None:
        raise SystemExit(f"User with firebase_uid={firebase_uid!r} was not found.")
    return user


async def delete_existing_history(session: AsyncSession, user_id: int, start: dt.datetime, end: dt.datetime) -> None:
    meal_ids = select(Meal.id).where(Meal.user_id == user_id, Meal.created_at >= start, Meal.created_at <= end)
    await session.execute(delete(MealItem).where(MealItem.meal_id.in_(meal_ids)))
    await session.execute(delete(Meal).where(Meal.user_id == user_id, Meal.created_at >= start, Meal.created_at <= end))
    await session.execute(
        delete(BurnedCalories).where(
            BurnedCalories.user_id == user_id,
            BurnedCalories.created_at >= start,
            BurnedCalories.created_at <= end,
        )
    )
    await session.execute(
        delete(DailySummary).where(
            DailySummary.user_id == user_id,
            DailySummary.date >= start.date(),
            DailySummary.date <= end.date(),
        )
    )


async def seed_history(firebase_uid: str, days: int, append: bool, seed: int | None) -> None:
    if days < 1:
        raise SystemExit("--days must be greater than 0.")

    today = dt.datetime.now(dt.UTC).date()
    start_day = today - dt.timedelta(days=days - 1)
    start_dt = dt.datetime.combine(start_day, dt.time.min, tzinfo=dt.UTC)
    end_dt = dt.datetime.combine(today, dt.time.max, tzinfo=dt.UTC)
    stable_seed = int(hashlib.sha256(firebase_uid.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed if seed is not None else stable_seed)

    async with SessionLocal() as session:
        user = await get_user(session, firebase_uid)

        if not append:
            await delete_existing_history(session, user.id, start_dt, end_dt)
            await session.flush()

        for offset in range(days):
            day = start_day + dt.timedelta(days=offset)
            weekday = day.weekday()

            templates = [
                rng.choice(BREAKFASTS),
                rng.choice(LUNCHES),
                rng.choice(DINNERS),
            ]
            if rng.random() < 0.75:
                templates.insert(2, rng.choice(SNACKS))

            for template in templates:
                session.add(build_meal(user.id, day, template, rng))

            if weekday < 5 or rng.random() < 0.55:
                session.add(build_activity(user.id, day, rng.choice(ACTIVITIES), rng))

            await session.flush()
            await SummaryService(session).sync_daily_summary(user.id, day)

        await session.commit()

    mode = "appended" if append else "replaced"
    print(
        f"Seeded {days} days for firebase_uid={firebase_uid!r} ({start_day.isoformat()}..{today.isoformat()}, {mode})."
    )


def main() -> None:
    args = parse_args()
    asyncio.run(seed_history(args.firebase_uid, args.days, args.append, args.seed))


if __name__ == "__main__":
    main()
