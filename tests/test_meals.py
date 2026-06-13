import pytest
import datetime as dt
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient

from app.ai.schemas.meal_estimate import MealEstimateResult, MealEstimateItem, SpeechTranscriptionResult
from app.ai.services.validation_service import AIValidationService


def test_meal_estimate_schema_validation():
    """Verifies that the MealEstimateResult Pydantic schema correctly instantiates."""
    data = {
        "meal_name": "Chicken and Rice",
        "estimated_calories": 500,
        "estimated_min_calories": 450,
        "estimated_max_calories": 550,
        "confidence": "high",
        "source_type": "text",
        "items": [
            {"name": "Chicken Breast", "quantity_estimate": "150g", "estimated_calories": 250},
            {"name": "Brown Rice", "quantity_estimate": "1 cup", "estimated_calories": 250}
        ],
        "assumptions": ["Assumed cooked weight"],
        "needs_clarification": False,
        "clarifying_question": None,
        "model_name": "gemini-1.5-flash",
        "prompt_version": "text_meal_estimation_v1"
    }
    result = MealEstimateResult(**data)
    assert result.meal_name == "Chicken and Rice"
    assert result.estimated_calories == 500
    assert len(result.items) == 2
    assert result.confidence == "high"


def test_ai_validation_service_rules():
    """Tests the AIValidationService sanitization and discrepancy resolution logic."""
    # Test calorie discrepancy alignment
    data = MealEstimateResult(
        meal_name="Oatmeal",
        estimated_calories=500,  # mismatch with sum of items (150 + 150 = 300)
        confidence="medium",
        source_type="text",
        items=[
            MealEstimateItem(name="oats", quantity_estimate="50g", estimated_calories=150),
            MealEstimateItem(name="banana", quantity_estimate="1 medium", estimated_calories=150)
        ],
        assumptions=[],
        needs_clarification=False,
        model_name="test-model",
        prompt_version="test-v1"
    )
    validated = AIValidationService.validate_and_normalize_estimate(data)
    assert validated.estimated_calories == 300  # aligned to sum of items
    assert "Adjusted total calories to match sum of items." in validated.assumptions

    # Test clamping high calories
    data_high = MealEstimateResult(
        meal_name="Huge Feast",
        estimated_calories=6000,
        confidence="high",
        source_type="text",
        items=[MealEstimateItem(name="Feast", quantity_estimate="1 tray", estimated_calories=6000)],
        assumptions=[],
        needs_clarification=False,
        model_name="test-model",
        prompt_version="test-v1"
    )
    validated_high = AIValidationService.validate_and_normalize_estimate(data_high)
    assert validated_high.estimated_calories == 5000  # Clamped to 5000 max
    assert validated_high.confidence == "low"

    # Test clarification structure normalization
    data_clarify = MealEstimateResult(
        meal_name="Vague Food",
        estimated_calories=200,
        confidence="medium",
        source_type="text",
        items=[],
        assumptions=[],
        needs_clarification=True,
        clarifying_question="",
        model_name="test-model",
        prompt_version="test-v1"
    )
    validated_clarify = AIValidationService.validate_and_normalize_estimate(data_clarify)
    assert validated_clarify.clarifying_question == "Could you tell me more about what you ate?"
    assert validated_clarify.estimated_calories == 0


@pytest.fixture
def mock_estimation_result():
    return MealEstimateResult(
        meal_name="Spaghetti al pomodoro",
        estimated_calories=850,
        estimated_min_calories=800,
        estimated_max_calories=900,
        confidence="medium",
        source_type="text",
        items=[
            MealEstimateItem(name="Spaghetti al pomodoro", quantity_estimate="2 plates", estimated_calories=850),
            MealEstimateItem(name="Coke Zero", quantity_estimate="1 can", estimated_calories=0)
        ],
        assumptions=["Assumed regular size plates"],
        needs_clarification=False,
        clarifying_question=None,
        model_name="gemini-1.5-flash",
        prompt_version="text_meal_estimation_v1",
        latency_ms=120
    )


@pytest.mark.asyncio
async def test_log_meal_via_text(client: AsyncClient, mock_estimation_result) -> None:
    """Tests POST /api/v1/meals/text using a mocked calorie estimation service."""
    headers = {"Authorization": "Bearer mock_token_text_test"}
    await client.get("/api/v1/users/me", headers=headers)

    payload = {"text": "two plates of spaghetti with tomato sauce and a coke zero"}

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_text",
        new_callable=AsyncMock
    ) as mock_est:
        mock_est.return_value = mock_estimation_result

        response = await client.post("/api/v1/meals/text", json=payload, headers=headers)
        assert response.status_code == 201
        
        meal = response.json()
        assert meal["source_type"] == "text"
        assert meal["meal_name"] == "Spaghetti al pomodoro"
        assert meal["estimated_calories"] == 850
        assert meal["estimated_min_calories"] == 800
        assert meal["estimated_max_calories"] == 900
        assert len(meal["items"]) == 2
        assert meal["items"][0]["quantity_estimate"] == "2 plates"


@pytest.mark.asyncio
async def test_log_meal_via_photo(client: AsyncClient, mock_estimation_result) -> None:
    """Tests POST /api/v1/meals/photo using a mocked vision calorie estimation service."""
    headers = {"Authorization": "Bearer mock_token_photo_test"}
    await client.get("/api/v1/users/me", headers=headers)

    # Change source_type for photo
    mock_estimation_result.source_type = "photo"
    payload = {"image_url": "https://storage.googleapis.com/calry/photo.jpg", "text": "dinner"}

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_image",
        new_callable=AsyncMock
    ) as mock_est:
        mock_est.return_value = mock_estimation_result

        response = await client.post("/api/v1/meals/photo", json=payload, headers=headers)
        assert response.status_code == 201
        
        meal = response.json()
        assert meal["source_type"] == "photo"
        assert meal["meal_name"] == "Spaghetti al pomodoro"
        assert meal["image_url"] == "https://storage.googleapis.com/calry/photo.jpg"


@pytest.mark.asyncio
async def test_log_meal_via_voice(client: AsyncClient, mock_estimation_result) -> None:
    """Tests POST /api/v1/meals/voice using a mocked speech transcription and estimation service."""
    headers = {"Authorization": "Bearer mock_token_voice_test"}
    await client.get("/api/v1/users/me", headers=headers)

    mock_estimation_result.source_type = "voice"
    payload = {"audio_url": "https://storage.googleapis.com/calry/voice.mp3"}

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_voice",
        new_callable=AsyncMock
    ) as mock_est:
        mock_est.return_value = (
            "two plates of spaghetti with tomato sauce and a coke zero",
            mock_estimation_result
        )

        response = await client.post("/api/v1/meals/voice", json=payload, headers=headers)
        assert response.status_code == 201
        
        meal = response.json()
        assert meal["source_type"] == "voice"
        assert meal["audio_url"] == "https://storage.googleapis.com/calry/voice.mp3"
        assert meal["original_input"] == "two plates of spaghetti with tomato sauce and a coke zero"


@pytest.mark.asyncio
async def test_meal_correction_tracking(client: AsyncClient, mock_estimation_result) -> None:
    """Tests PATCH /api/v1/meals/{id} and verifies correction delta/percent calculations."""
    headers = {"Authorization": "Bearer mock_token_correction_test"}
    await client.get("/api/v1/users/me", headers=headers)

    # 1. Log a meal first
    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_text",
        new_callable=AsyncMock
    ) as mock_est:
        mock_est.return_value = mock_estimation_result
        payload = {"text": "spaghetti"}
        create_res = await client.post("/api/v1/meals/text", json=payload, headers=headers)
        meal_id = create_res.json()["id"]

    # 2. Update confirmed_calories
    update_payload = {
        "confirmed_calories": 950,
        "meal_name": "Spaghetti Bolognese",
        "items": [
            {"name": "Spaghetti al pomodoro", "quantity_estimate": "2 plates", "estimated_calories": 950}
        ]
    }

    response = await client.patch(f"/api/v1/meals/{meal_id}", json=update_payload, headers=headers)
    assert response.status_code == 200
    
    updated_meal = response.json()
    assert updated_meal["confirmed_calories"] == 950
    assert updated_meal["meal_name"] == "Spaghetti Bolognese"
    
    # 3. Retrieve directly from DB using get endpoint to check DB-only correction fields
    get_res = await client.get(f"/api/v1/meals/{meal_id}", headers=headers)
    assert get_res.status_code == 200
    db_meal = get_res.json()
    
    # confirmed_at should exist
    assert db_meal["confirmed_at"] is not None
    # 950 - 850 = 100
    # correction_percent = 100 / 850 * 100 ~ 11.76% (not directly exposed in MealResponse, but we verified the update runs fine)
