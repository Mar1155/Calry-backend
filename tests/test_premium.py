import datetime as dt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.meal import Meal
from app.models.daily_summary import DailySummary
from app.repositories.user import UserRepository


@pytest.mark.asyncio
async def test_premium_sync_and_status(client: AsyncClient) -> None:
    """Verifies syncing and retrieving premium status details via REST endpoints."""
    headers = {"Authorization": "Bearer mock_token_premium_sync_test"}

    # 1. Access profile to auto-register
    await client.get("/api/v1/users/me", headers=headers)

    # 2. Verify initially not premium
    status_response = await client.get("/api/v1/premium/status", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["is_premium"] is False

    # 3. Synchronize premium status from RevenueCat client payload
    sync_payload = {
        "is_premium": True,
        "entitlement": "premium",
        "expires_at": "2030-01-01T00:00:00Z",
        "revenuecat_app_user_id": "premium_sync_test_uid",
    }
    sync_response = await client.post("/api/v1/premium/sync", json=sync_payload, headers=headers)
    assert sync_response.status_code == 200
    assert sync_response.json()["is_premium"] is True
    assert sync_response.json()["entitlement"] == "premium"

    # 4. Verify updated status
    status_response2 = await client.get("/api/v1/premium/status", headers=headers)
    assert status_response2.status_code == 200
    assert status_response2.json()["is_premium"] is True
    assert status_response2.json()["entitlement"] == "premium"


@pytest.mark.asyncio
async def test_free_versus_premium_history_gating(client: AsyncClient, db_session: AsyncSession) -> None:
    """Verifies that free users only retrieve past 7 days of history, while premium gets full range."""
    # Register test user
    headers = {"Authorization": "Bearer mock_token_history_gate_test"}
    profile_res = await client.get("/api/v1/users/me", headers=headers)
    user_id = profile_res.json()["id"]

    today = dt.date.today()
    old_date = today - dt.timedelta(days=10)

    # 1. Create a historical summary & meal in database from 10 days ago (manually inserting via DB session)
    old_summary = DailySummary(
        user_id=user_id,
        date=old_date,
        consumed_calories=1500,
        burned_calories=200,
        remaining_calories=700,
    )
    db_session.add(old_summary)

    old_meal = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Old Pasta",
        meal_name="Pasta",
        estimated_calories=500,
        created_at=dt.datetime.combine(old_date, dt.time(12, 0)).replace(tzinfo=dt.UTC),
    )
    db_session.add(old_meal)
    await db_session.commit()

    # 2. Fetch as FREE user (user is not premium yet)
    # Check Meals history
    meals_res = await client.get("/api/v1/meals", headers=headers)
    assert meals_res.status_code == 200
    assert len(meals_res.json()) == 0  # Gated/truncated since it's 10 days old

    # Check Summaries history
    summaries_res = await client.get("/api/v1/summary/history", headers=headers)
    assert summaries_res.status_code == 200
    assert len(summaries_res.json()) == 0  # Gated/truncated since it's 10 days old

    # 3. Elevate user to premium status
    sync_payload = {
        "is_premium": True,
        "entitlement": "premium",
        "expires_at": "2030-01-01T00:00:00Z",
        "revenuecat_app_user_id": "premium_user_uid",
    }
    await client.post("/api/v1/premium/sync", json=sync_payload, headers=headers)

    # 4. Fetch as PREMIUM user
    # Check Meals history
    meals_res_premium = await client.get("/api/v1/meals", headers=headers)
    assert meals_res_premium.status_code == 200
    assert len(meals_res_premium.json()) == 1
    assert meals_res_premium.json()[0]["original_input"] == "Old Pasta"

    # Check Summaries history
    summaries_res_premium = await client.get("/api/v1/summary/history", headers=headers)
    assert summaries_res_premium.status_code == 200
    assert len(summaries_res_premium.json()) == 1
    assert summaries_res_premium.json()[0]["consumed_calories"] == 1500
