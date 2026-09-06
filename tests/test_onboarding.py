import pytest
from httpx import AsyncClient

HEADERS = {"Authorization": "Bearer mock_token_user_abc_123"}


def payload(**changes):
    value = {
        "goal_type": "lose", "formula_profile": "male", "age": 28,
        "height_cm": 180, "weight_kg": 75, "activity_level": "moderate",
        "target_pace": "gradual", "preferred_unit_system": "metric",
    }
    value.update(changes)
    return value


@pytest.mark.asyncio
async def test_calculation_uses_activity_and_pace(client: AsyncClient):
    response = await client.post("/api/v1/onboarding/calculate-target", json=payload(), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["maintenance_calories"] == 2610
    assert response.json()["suggested_target"] == 2350


@pytest.mark.asyncio
async def test_complete_persists_account_status(client: AsyncClient):
    response = await client.post("/api/v1/onboarding/complete", json=payload(selected_target=2350, target_was_manually_adjusted=False), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    status = await client.get("/api/v1/onboarding/status", headers=HEADERS)
    assert status.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_low_manual_target_requires_confirmation(client: AsyncClient):
    response = await client.post("/api/v1/onboarding/complete", json=payload(selected_target=1000), headers=HEADERS)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_completion_retry_preserves_profile_and_offer(client):
    data = payload(selected_target=2350, onboarding_version=3, journey_id="a" * 32, started_at="2026-09-01T12:00:00Z")
    assert (await client.post("/api/v1/onboarding/complete", json=data, headers=HEADERS)).status_code == 200
    profile = (await client.get("/api/v1/users/me", headers=HEADERS)).json()
    assert profile["onboarding_offer_status"] == "pending"
    assert profile["onboarding_journey_id"] == "a" * 32
    assert not profile["has_confirmed_meals"]
    assert (await client.post("/api/v1/onboarding/offer/handled", headers=HEADERS)).status_code == 204
    retry = await client.post("/api/v1/onboarding/complete", json={**data, "selected_target": 2800, "weight_kg": 90}, headers=HEADERS)
    assert retry.status_code == 200
    profile = (await client.get("/api/v1/users/me", headers=HEADERS)).json()
    assert profile["daily_calorie_goal"] == 2350
    assert profile["weight_kg"] == 75
    assert profile["onboarding_offer_status"] == "handled"


@pytest.mark.asyncio
async def test_name_edit_preserves_activity_target_and_manual_target_survives_goal_edit(client):
    await client.post("/api/v1/onboarding/complete", json=payload(selected_target=2350), headers=HEADERS)
    response = await client.patch("/api/v1/users/me", json={"name": "Mario"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["daily_calorie_goal"] == 2350
    assert response.json()["calorie_target_source"] == "calculated"
    response = await client.patch("/api/v1/users/me", json={"daily_calorie_goal": 2500, "weight_kg": 80}, headers=HEADERS)
    assert response.json()["calorie_target_source"] == "user_adjusted"
    response = await client.patch("/api/v1/users/me", json={"goal_type": "gain"}, headers=HEADERS)
    assert response.json()["daily_calorie_goal"] == 2500


@pytest.mark.asyncio
async def test_profile_guardrail_matches_onboarding(client):
    await client.post("/api/v1/onboarding/complete", json=payload(selected_target=2350), headers=HEADERS)
    response = await client.patch("/api/v1/users/me", json={"daily_calorie_goal": 1000}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TARGET_CONFIRMATION_REQUIRED"
    response = await client.patch("/api/v1/users/me", json={"daily_calorie_goal": 1000, "unsafe_target_confirmed": True}, headers=HEADERS)
    assert response.status_code == 200


def test_calorie_rounding_matches_dart_ties():
    from app.services.calorie_target_service import CalorieTargetService
    assert CalorieTargetService.calculate_daily_target(2025, "maintain") == 2050
    assert CalorieTargetService.calculate_daily_target(2075, "maintain") == 2100


@pytest.mark.asyncio
async def test_anonymous_events_are_idempotent_and_reject_answers(client, db_session):
    import datetime as dt
    from sqlalchemy import select, func
    from app.models.onboarding_event import OnboardingEvent
    event = {"event_id": "b" * 32, "journey_id": "c" * 32, "event_name": "step_viewed", "step": "age", "locale": "it", "platform": "ios", "occurred_at": dt.datetime.now(dt.UTC).isoformat()}
    for _ in range(2):
        assert (await client.post("/api/v1/onboarding/events", json={"events": [event]})).status_code == 204
    assert await db_session.scalar(select(func.count()).select_from(OnboardingEvent)) == 1
    assert (await client.post("/api/v1/onboarding/events", json={"events": [{**event, "age": 28}]})).status_code == 422
    assert (await client.post("/api/v1/onboarding/events", json={"events": [{**event, "event_name": "purchase_confirmed"}]})).status_code == 422


@pytest.mark.asyncio
async def test_account_switch_cannot_complete_or_handle_another_journey(client):
    response = await client.post('/api/v1/onboarding/complete', json=payload(selected_target=2350, owner_uid='different-account'), headers=HEADERS)
    assert response.status_code == 409
    assert (await client.get('/api/v1/onboarding/status', headers=HEADERS)).json()['status'] != 'completed'
    await client.post('/api/v1/onboarding/complete', json=payload(selected_target=2350, onboarding_version=3, journey_id='d' * 32), headers=HEADERS)
    response = await client.post('/api/v1/onboarding/offer/handled', json={'journey_id':'e' * 32}, headers=HEADERS)
    assert response.status_code == 409
    assert (await client.get('/api/v1/users/me', headers=HEADERS)).json()['onboarding_offer_status'] == 'pending'
