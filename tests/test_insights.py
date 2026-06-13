import datetime as dt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_summary import DailySummary
from app.models.meal import Meal


@pytest.mark.asyncio
async def test_insights_gating_for_free_users(client: AsyncClient) -> None:
    """Verifies that free users cannot access premium insights and summaries (403 Forbidden)."""
    headers = {"Authorization": "Bearer mock_token_free_insights_test"}

    # Register user automatically by fetching profile
    await client.get("/api/v1/users/me", headers=headers)

    # 1. Check weekly summary endpoint gating
    weekly_res = await client.get("/api/v1/summary/weekly", headers=headers)
    assert weekly_res.status_code == 403
    assert "Premium subscription required" in weekly_res.json()["detail"]

    # 2. Check patterns endpoint gating
    patterns_res = await client.get("/api/v1/insights/patterns", headers=headers)
    assert patterns_res.status_code == 403
    assert "Premium subscription required" in patterns_res.json()["detail"]


@pytest.mark.asyncio
async def test_insights_success_for_premium_users(client: AsyncClient, db_session: AsyncSession) -> None:
    """Verifies that premium users can successfully retrieve weekly reports and pattern insights."""
    headers = {"Authorization": "Bearer mock_token_premium_insights_test"}

    # 1. Register user and upgrade to premium status
    profile_res = await client.get("/api/v1/users/me", headers=headers)
    user_id = profile_res.json()["id"]

    sync_payload = {
        "is_premium": True,
        "entitlement": "premium",
        "expires_at": "2030-01-01T00:00:00Z",
        "revenuecat_app_user_id": "premium_insights_uid",
    }
    await client.post("/api/v1/premium/sync", json=sync_payload, headers=headers)

    # 2. Populate daily summaries for the past few days to calculate stats
    today = dt.date.today()
    
    # Add summaries for the last 3 days
    s1 = DailySummary(
        user_id=user_id,
        date=today,
        consumed_calories=2000,
        burned_calories=200,
        remaining_calories=200, # goal target would be 2000 + 200 - 200 = 2000, ratio 1.0 (within target)
    )
    s2 = DailySummary(
        user_id=user_id,
        date=today - dt.timedelta(days=1),
        consumed_calories=1800,
        burned_calories=100,
        remaining_calories=300, # goal target would be 1800 + 300 - 100 = 2000, ratio 0.9 (within target)
    )
    s3 = DailySummary(
        user_id=user_id,
        date=today - dt.timedelta(days=2),
        consumed_calories=2500,
        burned_calories=500,
        remaining_calories=0, # goal target is 2000, ratio 1.25 (outside target)
    )
    db_session.add_all([s1, s2, s3])

    # 3. Add meals to compute most frequent meal name
    m1 = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Chicken Salad",
        meal_name="Chicken Salad",
        estimated_calories=400,
        created_at=dt.datetime.combine(today, dt.time(12, 0)).replace(tzinfo=dt.UTC),
    )
    m2 = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Chicken Salad for dinner",
        meal_name="Chicken Salad",
        estimated_calories=500,
        created_at=dt.datetime.combine(today - dt.timedelta(days=1), dt.time(19, 0)).replace(tzinfo=dt.UTC),
    )
    m3 = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Greek Yogurt",
        meal_name="Yogurt",
        estimated_calories=200,
        created_at=dt.datetime.combine(today - dt.timedelta(days=2), dt.time(8, 0)).replace(tzinfo=dt.UTC),
    )
    db_session.add_all([m1, m2, m3])
    await db_session.commit()

    # 4. Request weekly report
    weekly_res = await client.get("/api/v1/summary/weekly", headers=headers)
    assert weekly_res.status_code == 200
    data = weekly_res.json()
    
    # Math check:
    # average_calories = (2000 + 1800 + 2500) / 3 = 2100
    # highest_calories = 2500
    # lowest_calories = 1800
    # days_within_target = 2 (s1 with ratio 1.0, s2 with ratio 0.9. s3 has ratio 1.25)
    # most_frequent_meal = "Chicken Salad"
    assert data["average_calories"] == 2100
    assert data["days_within_target"] == 2
    assert data["highest_calories"] == 2500
    assert data["lowest_calories"] == 1800
    assert data["most_frequent_meal"] == "Chicken Salad"
    assert "ai_observation" in data

    # 5. Request pattern insights
    patterns_res = await client.get("/api/v1/insights/patterns", headers=headers)
    assert patterns_res.status_code == 200
    patterns_data = patterns_res.json()
    assert "patterns" in patterns_data
    assert isinstance(patterns_data["patterns"], list)
    assert len(patterns_data["patterns"]) > 0
