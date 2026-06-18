import datetime as dt
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas.meal_completion import MealCompletionResult, MealSuggestionItem
from app.models.meal import Meal


@pytest.fixture
def mock_completion_result():
    return MealCompletionResult(
        suggestions=[
            MealSuggestionItem(
                meal_name="Insalata di Salmone e Avocado",
                description="Un'insalata fresca e nutriente ricca di grassi buoni e proteine.",
                estimated_calories=450,
                protein_g=30.0,
                carbs_g=10.0,
                fat_g=32.0,
                ingredients=["150g salmone grigliato", "1/2 avocado", "Lattuga e pomodorini"],
                preparation_hint="Griglia il salmone e uniscilo all'avocado tagliato a cubetti su un letto di insalata.",
                reasoning="Ottima per completare l'apporto proteico e di grassi sani senza eccedere nei carboidrati.",
                meal_type="dinner",
                difficulty="easy",
                prep_time_minutes=15,
            ),
            MealSuggestionItem(
                meal_name="Yogurt Greco con Noci e Miele",
                description="Uno snack cremoso e bilanciato.",
                estimated_calories=250,
                protein_g=15.0,
                carbs_g=20.0,
                fat_g=12.0,
                ingredients=["150g yogurt greco 0%", "20g noci", "1 cucchiaino di miele"],
                preparation_hint="Unisci tutti gli ingredienti in una ciotola e mescola.",
                reasoning="Fornisce proteine a lento rilascio e una moderata quantità di carboidrati e grassi sani.",
                meal_type="snack",
                difficulty="easy",
                prep_time_minutes=5,
            ),
        ],
        daily_context_summary="Hai consumato 1200/2000 kcal, ti mancano 800 kcal.",
        macro_balance_note="Sei un po' basso in proteine oggi, questi suggerimenti compensano.",
        model_name="gemini-2.5-flash",
        prompt_version="meal_completion_v1",
        latency_ms=150,
    )


@pytest.mark.asyncio
async def test_meal_completion_success(client: AsyncClient, db_session: AsyncSession, mock_completion_result) -> None:
    """Tests POST /api/v1/meals/complete-day when remaining calories are > 200."""
    headers = {"Authorization": "Bearer mock_token_completion_test"}

    # 1. Register user
    profile_res = await client.get("/api/v1/users/me", headers=headers)
    user_id = profile_res.json()["id"]

    # 2. Add today's meal consuming 1200 calories
    today = dt.date.today()
    meal = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Big Lunch",
        meal_name="Lunch",
        estimated_calories=1200,
        total_protein_g=40.0,
        total_carbs_g=150.0,
        total_fat_g=45.0,
        created_at=dt.datetime.combine(today, dt.time(12, 0)).replace(tzinfo=dt.UTC),
    )
    db_session.add(meal)
    await db_session.commit()

    # 3. Patch the AICalorieEstimationService.suggest_meal_completion method
    with patch(
        "app.api.v1.routes.meal_completion.AICalorieEstimationService.suggest_meal_completion",
        new_callable=AsyncMock
    ) as mock_complete:
        mock_complete.return_value = mock_completion_result

        # 4. Trigger the route
        response = await client.post("/api/v1/meals/complete-day", headers=headers)
        assert response.status_code == 200

        data = response.json()
        assert len(data["suggestions"]) == 2
        assert data["suggestions"][0]["meal_name"] == "Insalata di Salmone e Avocado"
        assert data["suggestions"][0]["estimated_calories"] == 450
        assert data["remaining_calories"] == 800  # 2000 - 1200 = 800
        assert data["consumed_calories"] == 1200
        assert data["daily_goal"] == 2000


@pytest.mark.asyncio
async def test_meal_completion_guardrail(client: AsyncClient, db_session: AsyncSession) -> None:
    """Tests POST /api/v1/meals/complete-day when remaining calories are < 200 (guardrail)."""
    headers = {
        "Authorization": "Bearer mock_token_completion_guardrail_test",
        "Accept-Language": "it",
    }

    # 1. Register user
    profile_res = await client.get("/api/v1/users/me", headers=headers)
    user_id = profile_res.json()["id"]

    # 2. Add today's meal consuming 1850 calories
    today = dt.date.today()
    meal = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Huge Feast",
        meal_name="Feast",
        estimated_calories=1850,
        total_protein_g=80.0,
        total_carbs_g=220.0,
        total_fat_g=70.0,
        created_at=dt.datetime.combine(today, dt.time(12, 0)).replace(tzinfo=dt.UTC),
    )
    db_session.add(meal)
    await db_session.commit()

    # 3. Patch the AI service to verify it is NOT called
    with patch(
        "app.api.v1.routes.meal_completion.AICalorieEstimationService.suggest_meal_completion",
        new_callable=AsyncMock
    ) as mock_complete:
        # 4. Trigger the route
        response = await client.post("/api/v1/meals/complete-day", headers=headers)
        assert response.status_code == 200

        # AI should not have been called
        mock_complete.assert_not_called()

        data = response.json()
        assert len(data["suggestions"]) == 0
        assert "Hai quasi raggiunto il tuo obiettivo" in data["daily_context_summary"]
        assert data["remaining_calories"] == 150  # 2000 - 1850 = 150
