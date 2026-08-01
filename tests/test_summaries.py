import datetime as dt

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_water_glasses_flow(client: AsyncClient) -> None:
    """Water counter: starts at zero, one tap adds one, never goes negative."""
    headers = {"Authorization": "Bearer mock_token_water_test"}
    await client.get("/api/v1/users/me", headers=headers)

    res = await client.get("/api/v1/summary/today", headers=headers)
    assert res.status_code == 200
    assert res.json()["water_glasses"] == 0

    res = await client.post("/api/v1/summary/water", json={"delta": 1}, headers=headers)
    assert res.status_code == 200
    assert res.json()["water_glasses"] == 1

    # Empty body defaults to one glass
    res = await client.post("/api/v1/summary/water", json={}, headers=headers)
    assert res.status_code == 200
    assert res.json()["water_glasses"] == 2

    # Undo past zero clamps at zero
    res = await client.post("/api/v1/summary/water", json={"delta": -5}, headers=headers)
    assert res.status_code == 200
    assert res.json()["water_glasses"] == 0

    # Oversized deltas rejected by validation
    res = await client.post("/api/v1/summary/water", json={"delta": 50}, headers=headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_summary_can_be_loaded_for_an_accessible_date(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer mock_token_summary_date"}
    await client.get("/api/v1/users/me", headers=headers)

    selected_date = dt.date.today() - dt.timedelta(days=1)
    res = await client.get(f"/api/v1/summary/date/{selected_date.isoformat()}", headers=headers)

    assert res.status_code == 200
    assert res.json()["date"] == selected_date.isoformat()
    assert res.json()["consumed_calories"] == 0

    future_date = dt.date.today() + dt.timedelta(days=1)
    future_res = await client.get(f"/api/v1/summary/date/{future_date.isoformat()}", headers=headers)
    assert future_res.status_code == 422
