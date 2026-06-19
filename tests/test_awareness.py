import datetime as dt

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_summary import DailySummary
from app.services.awareness_service import AwarenessService


@pytest.mark.asyncio
async def test_awareness_today_empty_is_open_no_negative(client: AsyncClient) -> None:
    """A fresh user has an open week — never a failed/zero-pressure state."""
    headers = {"Authorization": "Bearer mock_token_awareness_empty"}
    res = await client.get("/api/v1/awareness/today", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["has_logged_today"] is False
    assert data["aware_days_this_week"] == 0
    assert data["days_in_week"] == 7
    assert data["current_soft_streak"] == 0
    assert data["last_aware_date"] is None
    assert data["label"] == "0/7 days aware"


@pytest.mark.asyncio
async def test_awareness_counts_logged_days(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = {"Authorization": "Bearer mock_token_awareness_count"}
    profile = await client.get("/api/v1/users/me", headers=headers)
    user_id = profile.json()["id"]

    today = dt.date.today()
    week_start = today - dt.timedelta(days=today.weekday())
    # Two aware days this week: week_start and today (today only counts if in this week)
    dates = {week_start, today}
    db_session.add_all(
        [
            DailySummary(
                user_id=user_id, date=d, consumed_calories=1500, burned_calories=0, remaining_calories=500
            )
            for d in dates
        ]
    )
    await db_session.flush()

    res = await client.get("/api/v1/awareness/today", headers=headers)
    data = res.json()
    assert data["has_logged_today"] is True
    assert data["aware_today"] is True
    assert data["aware_days_this_week"] == len(dates)
    assert data["label"] == f"{len(dates)}/7 days aware"

    week_res = await client.get("/api/v1/awareness/week", headers=headers)
    wdata = week_res.json()
    assert len(wdata["days"]) == 7
    assert wdata["aware_days"] == len(dates)
    assert sum(1 for d in wdata["days"] if d["is_aware"]) == len(dates)


@pytest.mark.asyncio
async def test_zero_consumed_day_is_not_aware(client: AsyncClient, db_session: AsyncSession) -> None:
    """A summary row with no consumed calories (e.g. all meals deleted) is not aware."""
    headers = {"Authorization": "Bearer mock_token_awareness_zero"}
    profile = await client.get("/api/v1/users/me", headers=headers)
    user_id = profile.json()["id"]

    today = dt.date.today()
    db_session.add(
        DailySummary(user_id=user_id, date=today, consumed_calories=0, burned_calories=300, remaining_calories=2000)
    )
    await db_session.flush()

    data = (await client.get("/api/v1/awareness/today", headers=headers)).json()
    assert data["has_logged_today"] is False
    assert data["aware_days_this_week"] == 0


# --- Pure service logic (no HTTP / no DB) ----------------------------------


def test_soft_streak_consecutive_ending_today() -> None:
    today = dt.date(2026, 6, 18)
    aware = [today, today - dt.timedelta(days=1), today - dt.timedelta(days=2)]
    assert AwarenessService._soft_streak(aware, today) == 3


def test_soft_streak_lapsed_returns_zero_no_broken_state() -> None:
    today = dt.date(2026, 6, 18)
    # Last aware was 3 days ago — run lapsed, but never negative.
    aware = [today - dt.timedelta(days=3)]
    assert AwarenessService._soft_streak(aware, today) == 0


def test_soft_streak_grace_counts_from_yesterday() -> None:
    today = dt.date(2026, 6, 18)
    yesterday = today - dt.timedelta(days=1)
    aware = [yesterday, yesterday - dt.timedelta(days=1)]
    assert AwarenessService._soft_streak(aware, today) == 2


def test_label_format() -> None:
    assert AwarenessService._label(3) == "3/7 days aware"
    assert AwarenessService._label(0) == "0/7 days aware"
