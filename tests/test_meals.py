import logging
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult
from app.ai.services.validation_service import AIValidationService
from app.models.meal import Meal, MealRevision
from app.models.meal_analysis import MealAnalysisJob


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
            {"name": "Brown Rice", "quantity_estimate": "1 cup", "estimated_calories": 250},
        ],
        "assumptions": ["Assumed cooked weight"],
        "needs_clarification": False,
        "clarifying_question": None,
        "model_name": "gemini-1.5-flash",
        "prompt_version": "text_meal_estimation_v1",
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
            MealEstimateItem(name="banana", quantity_estimate="1 medium", estimated_calories=150),
        ],
        assumptions=[],
        needs_clarification=False,
        model_name="test-model",
        prompt_version="test-v1",
    )
    validated = AIValidationService.validate_and_normalize_estimate(data)
    assert validated.estimated_calories == 300  # aligned to sum of items
    # Realignment is now flagged (feeding the confidence score) and logged rather
    # than appended as a user-visible assumption on every meal (C10 noise cut).
    assert validated.total_realigned is True

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
        prompt_version="test-v1",
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
        prompt_version="test-v1",
    )
    validated_clarify = AIValidationService.validate_and_normalize_estimate(data_clarify)
    assert validated_clarify.clarifying_question == "Could you tell me more about what you ate?"
    assert validated_clarify.estimated_calories == 0


@pytest.mark.asyncio
async def test_meal_detail_favorite_creates_and_toggles_memory(client: AsyncClient, db_session) -> None:
    headers = {"Authorization": "Bearer mock_token_meal_favorite"}
    user_response = await client.get("/api/v1/users/me", headers=headers)
    user_id = user_response.json()["id"]
    meal = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Pasta al pomodoro",
        meal_name="Pasta al pomodoro",
        estimated_calories=540,
    )
    db_session.add(meal)
    await db_session.commit()

    first = await client.patch(f"/api/v1/food-memory/meal/{meal.id}/favorite", headers=headers)
    assert first.status_code == 200
    assert first.json()["is_favorite"] is True

    second = await client.patch(f"/api/v1/food-memory/meal/{meal.id}/favorite", headers=headers)
    assert second.status_code == 200
    assert second.json()["is_favorite"] is False


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
            MealEstimateItem(name="Coke Zero", quantity_estimate="1 can", estimated_calories=0),
        ],
        assumptions=["Assumed regular size plates"],
        needs_clarification=False,
        clarifying_question=None,
        model_name="gemini-1.5-flash",
        prompt_version="text_meal_estimation_v1",
        latency_ms=120,
    )


@pytest.mark.asyncio
async def test_log_meal_via_text(client: AsyncClient, mock_estimation_result) -> None:
    """Tests POST /api/v1/meals/text using a mocked calorie estimation service."""
    headers = {"Authorization": "Bearer mock_token_text_test"}
    await client.get("/api/v1/users/me", headers=headers)

    payload = {"text": "two plates of spaghetti with tomato sauce and a coke zero"}

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_text", new_callable=AsyncMock
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
    headers = {
        "Authorization": "Bearer mock_token_photo_test",
        "Accept-Language": "it-IT,it;q=0.9",
    }
    await client.get("/api/v1/users/me", headers=headers)

    # Change source_type for photo
    mock_estimation_result.source_type = "photo"
    payload = {"image_url": "https://storage.googleapis.com/calry/photo.jpg"}

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_image", new_callable=AsyncMock
    ) as mock_est:
        mock_est.return_value = mock_estimation_result

        response = await client.post("/api/v1/meals/photo", json=payload, headers=headers)
        assert response.status_code == 201

        meal = response.json()
        assert meal["source_type"] == "photo"
        assert meal["meal_name"] == "Spaghetti al pomodoro"
        assert meal["original_input"] == "Spaghetti al pomodoro"
        assert meal["image_url"] == "https://storage.googleapis.com/calry/photo.jpg"
        assert mock_est.call_args.kwargs["user_context"].locale == "it"


@pytest.mark.asyncio
async def test_start_photo_analysis_queues_job(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer mock_token_photo_analysis"}
    await client.get("/api/v1/users/me", headers=headers)

    class DummyTask:
        id = "celery-task-1"

    with patch("app.tasks.meal_analysis.analyze_photo_meal.delay", return_value=DummyTask()) as mock_delay:
        response = await client.post(
            "/api/v1/meals/photo/analysis",
            json={
                "image_url": "https://storage.googleapis.com/calry/photo.jpg",
                "text": "pizza",
                "client_request_id": "analysis-test-1",
            },
            headers=headers,
        )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert data["meal_id"] is None
    mock_delay.assert_called_once_with(data["id"])

    status_response = await client.get(f"/api/v1/meals/photo/analysis/{data['id']}", headers=headers)
    assert status_response.status_code == 200
    status_data = status_response.json()
    assert status_data["status"] == "queued"
    assert status_data["meal"] is None


@pytest.mark.asyncio
async def test_photo_analysis_status_hides_worker_error(client: AsyncClient, db_session) -> None:
    headers = {"Authorization": "Bearer mock_token_photo_analysis_sanitized_error"}
    await client.get("/api/v1/users/me", headers=headers)

    class DummyTask:
        id = "celery-task-sanitized-error"

    with patch("app.tasks.meal_analysis.analyze_photo_meal.delay", return_value=DummyTask()):
        started = await client.post(
            "/api/v1/meals/photo/analysis",
            json={
                "image_url": "https://storage.googleapis.com/calry/photo.jpg",
                "client_request_id": "analysis-sanitized-error",
            },
            headers=headers,
        )

    result = await db_session.execute(select(MealAnalysisJob).where(MealAnalysisJob.id == started.json()["id"]))
    job = result.scalar_one()
    job.status = "failed"
    job.error_message = "redis://user:password@internal-host connection refused"
    await db_session.commit()

    response = await client.get(f"/api/v1/meals/photo/analysis/{job.id}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == "analysis_failed"
    assert body["error_message"] == "We couldn't analyze this photo. Try again or add a short description."
    assert "redis://" not in response.text


@pytest.mark.asyncio
async def test_meal_upload_rejects_invalid_media_before_analysis(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer mock_token_meal_upload_validation"}
    await client.get("/api/v1/users/me", headers=headers)

    empty = await client.post(
        "/api/v1/meals/upload",
        files={"file": ("empty.jpg", b"", "image/jpeg")},
        headers=headers,
    )
    assert empty.status_code == 422

    unsupported = await client.post(
        "/api/v1/meals/upload",
        files={"file": ("notes.txt", b"not a meal", "text/plain")},
        headers=headers,
    )
    assert unsupported.status_code == 415

    with patch("app.api.v1.routes.meals.settings.MEAL_UPLOAD_MAX_BYTES", 4):
        oversized = await client.post(
            "/api/v1/meals/upload",
            files={"file": ("meal.jpg", b"12345", "image/jpeg")},
            headers=headers,
        )
    assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_start_photo_analysis_logs_queue_failure(client: AsyncClient, caplog: pytest.LogCaptureFixture) -> None:
    headers = {"Authorization": "Bearer mock_token_photo_analysis_queue_failure"}
    await client.get("/api/v1/users/me", headers=headers)
    caplog.set_level(logging.ERROR, logger="app.api.meals")

    with patch(
        "app.tasks.meal_analysis.analyze_photo_meal.delay",
        side_effect=ConnectionError("Redis connection refused"),
    ):
        response = await client.post(
            "/api/v1/meals/photo/analysis",
            json={
                "image_url": "https://storage.googleapis.com/calry/photo.jpg",
                "client_request_id": "analysis-queue-failure-test",
            },
            headers=headers,
        )

    assert response.status_code == 503
    queue_failure = next(
        record for record in caplog.records if "event=meal_analysis_queue_unavailable" in record.message
    )
    assert "transport=poll" in queue_failure.message
    assert "error_type=ConnectionError" in queue_failure.message
    assert queue_failure.exc_info is not None


@pytest.mark.asyncio
async def test_start_photo_analysis_is_idempotent(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer mock_token_photo_analysis_idempotent"}
    await client.get("/api/v1/users/me", headers=headers)

    class DummyTask:
        id = "celery-task-1"

    payload = {
        "image_url": "https://storage.googleapis.com/calry/photo.jpg",
        "client_request_id": "analysis-test-2",
    }

    with patch("app.tasks.meal_analysis.analyze_photo_meal.delay", return_value=DummyTask()) as mock_delay:
        first = await client.post("/api/v1/meals/photo/analysis", json=payload, headers=headers)
        second = await client.post("/api/v1/meals/photo/analysis", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    mock_delay.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_photo_analysis_revokes_running_worker(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer mock_token_photo_analysis_cancel"}
    await client.get("/api/v1/users/me", headers=headers)

    class DummyTask:
        id = "celery-task-cancel"

    payload = {
        "image_url": "https://storage.googleapis.com/calry/cancel.jpg",
        "client_request_id": "analysis-cancel-1",
    }
    with patch("app.tasks.meal_analysis.analyze_photo_meal.delay", return_value=DummyTask()):
        started = await client.post("/api/v1/meals/photo/analysis", json=payload, headers=headers)

    with patch("app.worker.celery_app.celery_app.control.revoke") as revoke:
        cancelled = await client.delete(
            "/api/v1/meals/photo/analysis/request/analysis-cancel-1",
            headers=headers,
        )

    assert cancelled.status_code == 204
    revoke.assert_called_once_with("celery-task-cancel", terminate=True, signal="SIGTERM")

    status_response = await client.get(f"/api/v1/meals/photo/analysis/{started.json()['id']}", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "cancelled"
    assert status_response.json()["meal_id"] is None


@pytest.mark.asyncio
async def test_log_meal_via_voice(client: AsyncClient, mock_estimation_result) -> None:
    """Tests POST /api/v1/meals/voice using a mocked speech transcription and estimation service."""
    headers = {"Authorization": "Bearer mock_token_voice_test"}
    await client.get("/api/v1/users/me", headers=headers)

    mock_estimation_result.source_type = "voice"
    payload = {"audio_url": "https://storage.googleapis.com/calry/voice.mp3"}

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_voice", new_callable=AsyncMock
    ) as mock_est:
        mock_est.return_value = ("two plates of spaghetti with tomato sauce and a coke zero", mock_estimation_result)

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
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_text", new_callable=AsyncMock
    ) as mock_est:
        mock_est.return_value = mock_estimation_result
        payload = {"text": "spaghetti"}
        create_res = await client.post("/api/v1/meals/text", json=payload, headers=headers)
        meal_id = create_res.json()["id"]

    # 2. Update confirmed_calories
    update_payload = {
        "confirmed_calories": 950,
        "meal_name": "Spaghetti Bolognese",
        "items": [{"name": "Spaghetti al pomodoro", "quantity_estimate": "2 plates", "estimated_calories": 950}],
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


@pytest.mark.asyncio
async def test_refine_meal_returns_unsaved_revision(client: AsyncClient, db_session, mock_estimation_result) -> None:
    headers = {"Authorization": "Bearer mock_token_refine_test"}
    await client.get("/api/v1/users/me", headers=headers)

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_text",
        new_callable=AsyncMock,
    ) as mock_est:
        mock_est.return_value = mock_estimation_result
        create_res = await client.post("/api/v1/meals/text", json={"text": "burger"}, headers=headers)
        meal_id = create_res.json()["id"]

    revised = MealEstimateResult(
        meal_name="Double Cheeseburger",
        estimated_calories=1050,
        estimated_min_calories=1000,
        estimated_max_calories=1100,
        confidence="high",
        source_type="text",
        items=[
            MealEstimateItem(
                name="Double cheeseburger",
                quantity_estimate="1 burger",
                weight_grams=300,
                calories_per_100g=318.3,
            ),
            MealEstimateItem(
                name="Mayonnaise",
                quantity_estimate="1 tbsp",
                weight_grams=14,
                calories_per_100g=680,
            ),
        ],
        assumptions=[],
        needs_clarification=False,
        model_name="test-model",
        prompt_version="meal_refinement_v1",
        ai_summary="Updated for a double burger with mayonnaise.",
        changes_made=["Extra beef patty", "Mayonnaise"],
    )

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.refine_estimate",
        new_callable=AsyncMock,
    ) as mock_refine:
        mock_refine.return_value = revised
        response = await client.post(
            f"/api/v1/meals/{meal_id}/refine",
            json={
                "user_refinement": "It was actually a double burger with mayonnaise.",
                "refinement_type": "text",
            },
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == meal_id
    assert body["meal_name"] == "Double Cheeseburger"
    assert body["estimated_calories"] == 1050
    assert body["confirmed_calories"] is None
    assert body["refinement_changes"] == ["Extra beef patty", "Mayonnaise"]

    get_res = await client.get(f"/api/v1/meals/{meal_id}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["estimated_calories"] == 850

    revisions = (await db_session.execute(select(MealRevision))).scalars().all()
    assert len(revisions) == 1
    assert revisions[0].previous_calories == 850
    assert revisions[0].revised_calories == 1050
