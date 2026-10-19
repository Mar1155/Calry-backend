"""Free-tier daily AI quota, Pro-only refinement and the memory teaser."""

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult
from app.dependencies import premium as premium_deps
from app.services.revenuecat_service import RevenueCatAPIError

PHOTO = {"image_url": "https://storage.googleapis.com/calry/photo.jpg"}


@pytest.fixture(autouse=True)
def _revenuecat_unavailable():
    """Free accounts stay free: the pre-denial RevenueCat re-check finds nothing."""
    with patch(
        "app.services.premium_service.PremiumService.refresh_from_revenuecat",
        new_callable=AsyncMock,
        side_effect=RevenueCatAPIError("unavailable in tests"),
    ):
        yield


def _estimate() -> MealEstimateResult:
    return MealEstimateResult(
        meal_name="Pizza",
        estimated_calories=800,
        estimated_min_calories=700,
        estimated_max_calories=900,
        confidence="medium",
        source_type="photo",
        items=[MealEstimateItem(name="Pizza", quantity_estimate="1", estimated_calories=800)],
        assumptions=[],
        needs_clarification=False,
        model_name="test-model",
        prompt_version="test",
    )


async def _user(client: AsyncClient, name: str, *, pro: bool = False) -> dict:
    headers = {"Authorization": f"Bearer mock_token_{name}"}
    await client.get("/api/v1/users/me", headers=headers)
    if pro:
        await client.post(
            "/api/v1/premium/sync",
            json={"is_premium": True, "revenuecat_app_user_id": f"{name}_uid"},
            headers=headers,
        )
    return headers


async def _photo(client: AsyncClient, headers: dict, **extra):
    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_image",
        new_callable=AsyncMock,
        return_value=_estimate(),
    ):
        return await client.post("/api/v1/meals/photo", json={**PHOTO, **extra}, headers=headers)


@pytest.mark.asyncio
async def test_free_user_gets_three_photo_analyses_then_quota_error(client: AsyncClient) -> None:
    headers = await _user(client, "quota_free_photo")

    for _ in range(3):
        assert (await _photo(client, headers)).status_code == 201

    response = await _photo(client, headers)

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error"] == "AI_QUOTA_EXCEEDED"
    assert detail["code"] == "AI_QUOTA_EXCEEDED"
    assert detail["limit"] == 3
    assert detail["remaining"] == 0
    reset_at = dt.datetime.fromisoformat(detail["reset_at"])
    assert reset_at.time() == dt.time.min
    assert reset_at.utcoffset() == dt.timedelta(0)


@pytest.mark.asyncio
async def test_quota_status_counts_down(client: AsyncClient) -> None:
    headers = await _user(client, "quota_status")

    before = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()
    await _photo(client, headers)
    after = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()

    assert before["unlimited"] is False
    assert before["limit"] == 3
    assert before["remaining"] == 3
    assert after["used"] == 1
    assert after["remaining"] == 2


@pytest.mark.asyncio
async def test_retry_with_same_request_id_is_not_charged_twice(client: AsyncClient) -> None:
    headers = await _user(client, "quota_idempotent")

    first = await _photo(client, headers, client_request_id="same-request")
    retry = await _photo(client, headers, client_request_id="same-request")
    quota = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()

    assert first.status_code == 201
    assert retry.json()["id"] == first.json()["id"]
    assert quota["used"] == 1


@pytest.mark.asyncio
async def test_failed_analysis_refunds_the_credit(client: AsyncClient) -> None:
    headers = await _user(client, "quota_refund")

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_image",
        new_callable=AsyncMock,
        side_effect=RuntimeError("provider down"),
    ):
        with pytest.raises(RuntimeError):
            await client.post("/api/v1/meals/photo", json=PHOTO, headers=headers)
    quota = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()

    assert quota["used"] == 0
    assert quota["remaining"] == 3


@pytest.mark.asyncio
async def test_cancelled_photo_job_refunds_the_credit(client: AsyncClient) -> None:
    headers = await _user(client, "quota_cancel")

    class DummyTask:
        id = "celery-task-quota"

    with patch("app.tasks.meal_analysis.analyze_photo_meal.delay", return_value=DummyTask()):
        started = await client.post(
            "/api/v1/meals/photo/analysis",
            json={**PHOTO, "client_request_id": "cancel-me"},
            headers=headers,
        )
    charged = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()
    with patch("app.worker.celery_app.celery_app.control.revoke"):
        cancelled = await client.delete("/api/v1/meals/photo/analysis/request/cancel-me", headers=headers)
    refunded = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()

    assert started.status_code == 202
    assert charged["used"] == 1
    assert cancelled.status_code == 204
    assert refunded["used"] == 0


@pytest.mark.asyncio
async def test_photo_analysis_job_is_blocked_after_quota(client: AsyncClient) -> None:
    headers = await _user(client, "quota_job")
    for _ in range(3):
        await _photo(client, headers)

    with patch("app.tasks.meal_analysis.analyze_photo_meal.delay") as mock_delay:
        response = await client.post("/api/v1/meals/photo/analysis", json=PHOTO, headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AI_QUOTA_EXCEEDED"
    mock_delay.assert_not_called()


@pytest.mark.asyncio
async def test_text_analysis_is_not_counted(client: AsyncClient) -> None:
    headers = await _user(client, "quota_text")
    estimate = _estimate()
    estimate.source_type = "text"

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_text",
        new_callable=AsyncMock,
        return_value=estimate,
    ):
        statuses = [
            (await client.post("/api/v1/meals/text", json={"text": "pizza"}, headers=headers)).status_code
            for _ in range(4)
        ]
    quota = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()

    assert statuses == [201] * 4
    assert quota["used"] == 0


@pytest.mark.asyncio
async def test_quota_resets_the_next_day(client: AsyncClient) -> None:
    headers = await _user(client, "quota_reset")
    for _ in range(3):
        await _photo(client, headers)
    assert (await _photo(client, headers)).status_code == 403

    tomorrow = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)
    with patch.object(premium_deps, "_ai_usage_today", return_value=tomorrow):
        response = await _photo(client, headers)

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_pro_user_has_no_quota(client: AsyncClient) -> None:
    headers = await _user(client, "quota_pro", pro=True)

    statuses = [(await _photo(client, headers)).status_code for _ in range(5)]
    quota = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()

    assert statuses == [201] * 5
    assert quota["unlimited"] is True
    assert quota["remaining"] is None


@pytest.mark.asyncio
async def test_free_user_cannot_refine_with_ai(client: AsyncClient) -> None:
    headers = await _user(client, "refine_free")
    meal = (await _photo(client, headers)).json()

    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.refine_estimate",
        new_callable=AsyncMock,
    ) as mock_refine:
        response = await client.post(
            f"/api/v1/meals/{meal['id']}/refine",
            json={"user_refinement": "no sauce", "refinement_type": "text"},
            headers=headers,
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_LOCKED_PRO"
    assert response.json()["detail"]["feature"] == "ai_refine"
    mock_refine.assert_not_called()


@pytest.mark.asyncio
async def test_memory_summary_is_locked_teaser_for_free(client: AsyncClient) -> None:
    headers = await _user(client, "memory_summary_free")

    response = await client.get(
        "/api/v1/memory/summary",
        headers={**headers, "Accept-Language": "it-IT"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["is_locked"] is True
    assert data["total_beliefs_count"] == 0
    assert data["sample_teaser"] == "Preferenze alimentari e abitudini rilevate"
    assert data["beliefs"] == []


@pytest.mark.asyncio
async def test_memory_summary_is_unlocked_for_pro(client: AsyncClient) -> None:
    headers = await _user(client, "memory_summary_pro", pro=True)

    response = await client.get("/api/v1/memory/summary", headers=headers)

    assert response.status_code == 200
    assert response.json()["is_locked"] is False


@pytest.mark.asyncio
async def test_stream_endpoints_return_plain_403_when_quota_is_used(client: AsyncClient) -> None:
    headers = await _user(client, "quota_streams")
    for _ in range(3):
        await _photo(client, headers)

    voice = await client.post(
        "/api/v1/meals/voice/stream",
        json={"audio_url": "https://storage.googleapis.com/calry/voice.m4a"},
        headers=headers,
    )
    photo = await client.post("/api/v1/meals/photo/stream", json=PHOTO, headers=headers)

    assert voice.status_code == 403
    assert voice.json()["detail"]["code"] == "AI_QUOTA_EXCEEDED"
    assert photo.status_code == 403
    assert photo.json()["detail"]["code"] == "AI_QUOTA_EXCEEDED"
