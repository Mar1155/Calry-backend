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
