import datetime as dt
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.text_normalization import canonicalize_food_name
from app.models.food_memory import UserFoodMemory
from app.models.meal import Meal

_DIGIT_TOKEN_RE = re.compile(r"\d+")


def _digit_tokens(text: str) -> set[str]:
    return set(_DIGIT_TOKEN_RE.findall(text or ""))


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

    async def get_cached_match(self, user_id: int, raw_text: str) -> UserFoodMemory | None:
        """Pre-inference cache lookup (C3/C19). Returns a confirmed memory whose
        canonical key matches the user's input — exact first, then a strict CPU-only
        fuzzy fallback — or None to fall through to the LLM."""
        canonical = canonicalize_food_name(raw_text)
        if not canonical:
            return None
        min_use = settings.FOOD_MEMORY_MIN_USE_COUNT

        # 1. Exact canonical match (highest-confidence, cheapest).
        exact = await self.db.execute(
            select(UserFoodMemory)
            .where(
                UserFoodMemory.user_id == user_id,
                UserFoodMemory.canonical_key == canonical,
                UserFoodMemory.use_count >= min_use,
            )
            .order_by(UserFoodMemory.use_count.desc())
        )
        row = exact.scalars().first()
        if row is not None:
            return row

        # 2. Strict fuzzy fallback over the user's own (small) memory set — no vector DB.
        if not settings.FOOD_MEMORY_FUZZY_ENABLED:
            return None
        try:
            from rapidfuzz import fuzz
        except ImportError:
            return None

        candidates = await self.db.execute(
            select(UserFoodMemory)
            .where(
                UserFoodMemory.user_id == user_id,
                UserFoodMemory.use_count >= min_use,
            )
            .order_by(UserFoodMemory.last_used_at.desc())
            .limit(50)
        )
        rows = list(candidates.scalars().all())
        if not rows:
            return None

        want_digits = _digit_tokens(canonical)
        best: UserFoodMemory | None = None
        best_score = 0.0
        for c in rows:
            key = c.canonical_key or canonicalize_food_name(c.display_name)
            # Quantity guard: don't fuzzy-merge "2 eggs" with "3 eggs".
            if want_digits and _digit_tokens(key) != want_digits:
                continue
            score = fuzz.token_set_ratio(canonical, key)
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score >= settings.FOOD_MEMORY_FUZZY_THRESHOLD:
            return best
        return None

    async def upsert_from_meal(self, meal: Meal, confirmed_calories: int) -> UserFoodMemory:
        """Create or update a food memory after the user confirms a meal (C20:
        outlier-clipped weighted average for calories, weighted-averaged macros)."""
        display_name = meal.meal_name or meal.original_input
        normalized = display_name.lower().strip()[:500]
        canonical = canonicalize_food_name(display_name)[:500]
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

        # Prefer merging by canonical key (collapses wording variants), then legacy
        # exact normalized name, before creating a fresh row.
        existing = None
        if canonical:
            res = await self.db.execute(
                select(UserFoodMemory).where(
                    UserFoodMemory.user_id == meal.user_id,
                    UserFoodMemory.canonical_key == canonical,
                )
            )
            existing = res.scalars().first()
        if existing is None:
            res = await self.db.execute(
                select(UserFoodMemory).where(
                    UserFoodMemory.user_id == meal.user_id,
                    UserFoodMemory.normalized_name == normalized,
                )
            )
            existing = res.scalar_one_or_none()

        if existing:
            old_count = existing.use_count
            new_count = old_count + 1

            # C20: soft-clip an outlier confirmation against the established prior.
            value = confirmed_calories
            if old_count >= 3:
                lo = existing.learned_calories * 0.5
                hi = existing.learned_calories * 2.0
                value = min(max(confirmed_calories, lo), hi)
            existing.learned_calories = round((existing.learned_calories * old_count + value) / new_count)

            # C20: weighted-average macros instead of overwriting with the latest meal.
            for attr, meal_val in (
                ("protein_g", meal.total_protein_g),
                ("carbs_g", meal.total_carbs_g),
                ("fat_g", meal.total_fat_g),
            ):
                if meal_val is None:
                    continue
                old_val = getattr(existing, attr)
                if old_val is not None:
                    setattr(existing, attr, round((old_val * old_count + meal_val) / new_count, 1))
                else:
                    setattr(existing, attr, round(meal_val, 1))

            existing.use_count = new_count
            existing.last_used_at = now
            existing.display_name = display_name
            existing.canonical_key = canonical or existing.canonical_key
            if items_snapshot:
                existing.items_snapshot = items_snapshot
            await self.db.flush()
            return existing

        entry = UserFoodMemory(
            user_id=meal.user_id,
            normalized_name=normalized,
            canonical_key=canonical or None,
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
