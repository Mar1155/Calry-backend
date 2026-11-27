"""Meal category for a new entry: the person's choice, then the meal they just
logged, then explicit wording, then the clock. Built from a real first day:
side dishes and fruit logged right after lunch or dinner were labelled
"snack" because the model judged each food alone."""

import datetime as dt
import uuid
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.ai.prompts.image_estimation import IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT
from app.ai.prompts.meal_estimation import TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT
from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult
from app.api.v1.routes.meals import _process_and_save_meal, _resolve_meal_category
from app.core.client_time import set_timezone
from app.models.user import User

ROME = ZoneInfo("Europe/Rome")


def _estimate(suggestion: str | None = None, confidence: str | None = None) -> MealEstimateResult:
    return MealEstimateResult(
        meal_name="Mela",
        estimated_calories=95,
        confidence="medium",
        source_type="text",
        meal_category_suggestion=suggestion,
        meal_category_confidence=confidence,
        items=[MealEstimateItem(name="Mela", weight_grams=180, calories_per_100g=52.0)],
        model_name="m",
        prompt_version="p",
    )


def at(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, 26, hour, minute, tzinfo=ROME)


def test_the_persons_choice_always_wins() -> None:
    assert _resolve_meal_category("dinner", _estimate("snack", "high"), at(10), previous_category="lunch") == "dinner"


def test_an_entry_right_after_a_meal_joins_that_meal() -> None:
    assert (
        _resolve_meal_category(None, _estimate("snack", "medium"), at(21, 48), previous_category="dinner") == "dinner"
    )


def test_food_type_alone_never_makes_a_mealtime_entry_a_snack() -> None:
    assert _resolve_meal_category(None, _estimate("snack", "medium"), at(21, 44)) == "dinner"
    assert _resolve_meal_category(None, _estimate("snack", "low"), at(13)) == "lunch"


def test_explicit_wording_beats_the_clock() -> None:
    assert _resolve_meal_category(None, _estimate("breakfast", "high"), at(15)) == "breakfast"
    assert _resolve_meal_category(None, _estimate("snack", "high"), at(13)) == "snack"


def test_medium_main_meal_labels_still_help_off_the_clock() -> None:
    # Cornetto and cappuccino at 11:30: the clock says lunch.
    assert _resolve_meal_category(None, _estimate("breakfast", "medium"), at(11, 30)) == "breakfast"


def test_snack_hours_keep_the_ai_snack_label() -> None:
    assert _resolve_meal_category(None, _estimate("snack", "medium"), at(17)) == "snack"


def test_prompts_read_dry_weight_and_leave_category_context_to_the_app() -> None:
    for prompt in (TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT, IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT):
        assert "is the dry (raw) weight" in prompt
        assert "never transfers" in prompt
        assert "at most low confidence" in prompt


async def _save(db, user, logged_at: dt.datetime, estimate: MealEstimateResult, *, confirm: bool = True):
    with (
        patch("app.api.v1.routes.meals.InsightVersionService") as versions,
        patch("app.tasks.memory.enqueue_memory_distillation"),
    ):
        versions.return_value.record = AsyncMock()
        meal = await _process_and_save_meal(
            db=db,
            user=user,
            source_type="text",
            original_input=estimate.meal_name,
            image_url=None,
            audio_url=None,
            estimation=estimate,
            logged_at=logged_at.astimezone(dt.UTC),
        )
    if confirm:
        meal.confirmed_at = logged_at.astimezone(dt.UTC) + dt.timedelta(seconds=20)
        await db.flush()
    return meal


@pytest.mark.asyncio
async def test_side_dishes_after_lunch_and_fruit_after_dinner_join_the_meal(db_session) -> None:
    set_timezone(ROME)
    user = User(firebase_uid=f"cat-{uuid.uuid4().hex[:8]}", email=f"c-{uuid.uuid4().hex[:8]}@x.io", name="C")
    db_session.add(user)
    await db_session.flush()

    pasta = await _save(db_session, user, at(13, 3), _estimate("lunch", "medium"))
    mozzarella = await _save(db_session, user, at(13, 8), _estimate("snack", "medium"))
    later_snack = await _save(db_session, user, at(17, 30), _estimate("snack", "medium"))
    dinner = await _save(db_session, user, at(20, 48), _estimate("dinner", "medium"))
    apple = await _save(db_session, user, at(21, 44), _estimate("snack", "medium"))

    assert pasta.meal_category == "lunch"
    assert mozzarella.meal_category == "lunch"
    assert later_snack.meal_category == "snack"
    assert dinner.meal_category == "dinner"
    assert apple.meal_category == "dinner"


@pytest.mark.asyncio
async def test_an_unconfirmed_preview_does_not_pull_the_next_entry(db_session) -> None:
    set_timezone(ROME)
    user = User(firebase_uid=f"cat-{uuid.uuid4().hex[:8]}", email=f"c-{uuid.uuid4().hex[:8]}@x.io", name="C")
    db_session.add(user)
    await db_session.flush()

    await _save(db_session, user, at(16, 50), _estimate("breakfast", "high"), confirm=False)
    banana = await _save(db_session, user, at(17), _estimate("snack", "medium"))

    assert banana.meal_category == "snack"
