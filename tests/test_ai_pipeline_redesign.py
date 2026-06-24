"""Tests for the AI pipeline redesign: canonicalization, deterministic confidence,
per-source bias, proportional density clamp, parsing robustness, and the
pre-inference food-memory cache."""
import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult
from app.ai.services.confidence_service import AIConfidenceService, bucket_confidence
from app.ai.services.correction_context_service import AICorrectionContextService
from app.ai.services.validation_service import AIValidationService
from app.core.text_normalization import canonicalize_food_name
from app.models.food_memory import UserFoodMemory
from app.models.meal import Meal
from app.models.user import User
from app.repositories.food_memory import FoodMemoryRepository

# ---- C4: canonicalizer ------------------------------------------------------

def test_canonicalize_merges_wording_variants():
    target = canonicalize_food_name("Oatmeal with banana")
    assert canonicalize_food_name("banana and oatmeal") == target
    assert canonicalize_food_name("oatmeal  with  a  banana!") == target
    assert canonicalize_food_name("Bananas, oatmeal") == target  # plural + punctuation


def test_canonicalize_distinguishes_real_differences():
    assert canonicalize_food_name("2 eggs") != canonicalize_food_name("3 eggs")
    assert canonicalize_food_name("chicken salad") != canonicalize_food_name("tuna salad")


def test_canonicalize_empty():
    assert canonicalize_food_name("") == ""
    assert canonicalize_food_name(None) == ""


# ---- C9: proportional density clamp (no pure-fat fabrication) ---------------

def test_density_clamp_scales_macros_proportionally():
    item = MealEstimateItem(
        name="Dense Mix", weight_grams=50, calories_per_100g=2000.0,
        protein_g=10.0, carbs_g=10.0, fat_g=60.0,
    )
    result = MealEstimateResult(
        meal_name="Dense", estimated_calories=1000, confidence="high", source_type="text",
        items=[item], model_name="t", prompt_version="t",
    )
    validated = AIValidationService.validate_and_normalize_estimate(result)
    it = validated.items[0]
    # Clamped to 900 kcal/100g -> 450 kcal for 50 g.
    assert it.estimated_calories == 450
    assert validated.density_clamped is True
    # Macros scaled DOWN proportionally, not zeroed into pure fat.
    assert it.protein_g > 0 and it.carbs_g > 0
    assert it.fat_g < 60.0


# ---- C10: range invariants --------------------------------------------------

def test_range_invariants_no_inversion():
    result = MealEstimateResult(
        meal_name="X", estimated_calories=999,
        estimated_min_calories=900, estimated_max_calories=1100,
        confidence="medium", source_type="text",
        items=[MealEstimateItem(name="rice", weight_grams=250, calories_per_100g=160.0)],
        model_name="t", prompt_version="t",
    )
    v = AIValidationService.validate_and_normalize_estimate(result)
    # Total realigned down to 400; min must not exceed the estimate.
    assert v.estimated_calories == 400
    assert v.estimated_min_calories <= v.estimated_calories <= v.estimated_max_calories


# ---- C12: deterministic confidence ------------------------------------------

def test_confidence_clarification_is_low():
    r = MealEstimateResult(
        meal_name="?", estimated_calories=0, confidence="high", source_type="text",
        needs_clarification=True, model_name="t", prompt_version="t",
    )
    assert AIConfidenceService.compute(r) <= 0.2


def test_confidence_degraded_is_capped():
    r = MealEstimateResult(
        meal_name="Burger", estimated_calories=600, confidence="high", source_type="text",
        degraded_extraction=True, model_name="t", prompt_version="t",
    )
    assert AIConfidenceService.compute(r) <= 0.3


def test_confidence_voice_capped_by_transcription():
    r = MealEstimateResult(
        meal_name="Salad", estimated_calories=300,
        estimated_min_calories=285, estimated_max_calories=315,
        confidence="high", source_type="voice",
        items=[MealEstimateItem(name="salad", weight_grams=200, calories_per_100g=150.0)],
        model_name="t", prompt_version="t",
    )
    high = AIConfidenceService.compute(r, transcription_confidence="high")
    low = AIConfidenceService.compute(r, transcription_confidence="low")
    assert low < high
    assert low <= 0.4  # capped by the shaky transcript


def test_bucket_thresholds():
    assert bucket_confidence(0.49) == "low"
    assert bucket_confidence(0.5) == "medium"
    assert bucket_confidence(0.85) == "high"


# ---- C8: parsing robustness -------------------------------------------------

def test_extract_json_strips_fences_and_prose():
    raw = 'Here you go:\n```json\n{"meal_name": "X", "estimated_calories": 100}\n```'
    cleaned = OpenRouterProvider._extract_json(raw)
    import json

    assert json.loads(cleaned)["estimated_calories"] == 100


def test_coerce_confidence_never_raises():
    assert OpenRouterProvider._coerce_confidence("HIGH") == "high"
    assert OpenRouterProvider._coerce_confidence("molto alta") == "medium"  # bad enum -> default
    assert OpenRouterProvider._coerce_confidence(None) == "medium"


# ---- C11: per-source systematic bias ----------------------------------------

def _meal(source, est, confirmed):
    pct = (confirmed - est) / est * 100 if est else 0.0
    return Meal(
        user_id=1, source_type=source, original_input="x", meal_name="x",
        estimated_calories=est, confirmed_calories=confirmed,
        correction_delta=confirmed - est, correction_percent=pct,
    )


def test_bias_per_source_requires_consistency_and_count():
    # 6 photo meals consistently under-estimated by ~25%; text mixed/insufficient.
    meals = [_meal("photo", 400, 500) for _ in range(6)]
    meals += [_meal("text", 500, 510), _meal("text", 500, 490)]  # only 2, below threshold
    bias = AICorrectionContextService._bias_by_source(meals)
    assert "photo" in bias and bias["photo"] > 0  # correct upward
    assert "text" not in bias  # not enough corrected meals


# ---- C3 / C20: pre-inference cache + robust memory update -------------------

@pytest.mark.asyncio
async def test_food_memory_cache_exact_hit(db_session: AsyncSession):
    user = User(firebase_uid="cache_uid", email="cache@x.io", name="C", daily_calorie_goal=2000)
    db_session.add(user)
    await db_session.flush()

    mem = UserFoodMemory(
        user_id=user.id,
        normalized_name="oatmeal with banana",
        canonical_key=canonicalize_food_name("Oatmeal with banana"),
        display_name="Oatmeal with banana",
        learned_calories=350,
        use_count=3,
        last_used_at=dt.datetime.now(dt.UTC),
        created_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(mem)
    await db_session.flush()

    repo = FoodMemoryRepository(db_session)
    hit = await repo.get_cached_match(user.id, "banana and oatmeal")
    assert hit is not None and hit.learned_calories == 350
    # Below min use_count is not served.
    miss = await repo.get_cached_match(user.id, "something never logged")
    assert miss is None


@pytest.mark.asyncio
async def test_food_memory_robust_update_clips_outlier(db_session: AsyncSession):
    user = User(firebase_uid="upd_uid", email="upd@x.io", name="U", daily_calorie_goal=2000)
    db_session.add(user)
    await db_session.flush()

    canonical = canonicalize_food_name("Test Food")
    mem = UserFoodMemory(
        user_id=user.id, normalized_name="test food", canonical_key=canonical,
        display_name="Test Food", learned_calories=400, use_count=3,
        last_used_at=dt.datetime.now(dt.UTC), created_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(mem)
    await db_session.flush()

    meal = Meal(user_id=user.id, source_type="text", original_input="Test Food", meal_name="Test Food",
                estimated_calories=400)
    repo = FoodMemoryRepository(db_session)
    # A wild 1200 kcal confirmation is soft-clipped to 2x (800) before averaging.
    updated = await repo.upsert_from_meal(meal, 1200)
    assert updated.use_count == 4
    assert updated.learned_calories == round((400 * 3 + 800) / 4)  # == 500
