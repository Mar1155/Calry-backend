"""Weight history and the adaptive calorie target."""
import datetime as dt

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.insights.features import DayFeatures
from app.models.daily_summary import DailySummary
from app.models.user import User
from app.services.weight_service import (
    WeightPoint,
    evaluate_adaptive_target,
    slope_kg_per_day,
    smoothed_trend,
)

TODAY = dt.date(2026, 11, 20)


def _day(offset: int, calories: int, goal: int = 2000, burned: int = 0, **macros) -> DayFeatures:
    return DayFeatures(
        date=TODAY - dt.timedelta(days=offset),
        calories=calories,
        goal_calories=goal,
        burned_calories=burned,
        water_glasses=0,
        meal_count=3 if calories else 0,
        meal_categories={},
        protein_g=macros.get("protein", 0.0),
        carbs_g=macros.get("carbs", 0.0),
        fat_g=macros.get("fat", 0.0),
        macro_meal_count=3 if macros else 0,
    )


def _window(calories: int = 2000, goal: int = 2000, logged: int = 28) -> list[DayFeatures]:
    return [_day(offset, calories if offset <= logged else 0, goal) for offset in range(28, 0, -1)]


def _weights(start_kg: float, kg_per_week: float, every: int = 3, days: int = 28) -> list[WeightPoint]:
    return [
        WeightPoint(TODAY - dt.timedelta(days=days - d), round(start_kg + kg_per_week * d / 7, 2))
        for d in range(0, days + 1, every)
    ]


def _evaluate(days, weights, **kwargs):
    defaults = {"current_goal": 2000, "goal_type": "lose", "target_pace": "balanced", "sex": "male"}
    return evaluate_adaptive_target(days=days, weights=weights, **{**defaults, **kwargs})


def test_trend_smooths_noise_and_slope_reads_rate():
    points = [WeightPoint(TODAY + dt.timedelta(days=i), w) for i, w in enumerate([80, 81, 80, 81, 80])]
    trend = smoothed_trend(points)
    assert trend[0] == 80
    assert max(trend) < 80.3
    falling = _weights(80, -0.5)
    assert slope_kg_per_day(falling) * 7 == pytest.approx(-0.5, abs=0.01)


def test_needs_enough_weigh_ins_over_two_weeks():
    result = _evaluate(_window(), _weights(80, -0.5)[-3:])
    assert result.status == "not_enough_weights"
    assert result.weigh_ins == 3


def test_needs_most_days_logged():
    result = _evaluate(_window(logged=15), _weights(80, 0))
    assert result.status == "not_enough_logging"


def test_off_target_days_block_the_suggestion():
    # Eating far above target explains the weight, not a wrong estimate.
    result = _evaluate(_window(calories=2600), _weights(80, 0.4))
    assert result.status == "low_adherence"


def test_macro_goals_count_toward_adherence():
    days = [_day(o, 2000, protein=60, carbs=250, fat=70) for o in range(28, 0, -1)]
    result = _evaluate(days, _weights(80, 0), protein_goal=150, carbs_goal=200, fat_goal=70)
    assert result.status == "low_adherence"
    days = [_day(o, 2000, protein=145, carbs=210, fat=68) for o in range(28, 0, -1)]
    result = _evaluate(days, _weights(80, 0), protein_goal=150, carbs_goal=200, fat_goal=70)
    assert result.status != "low_adherence"


def test_weight_stalled_on_target_suggests_lower_goal():
    # Planned -400 kcal/day (~-0.36 kg/week) but weight is flat:
    # maintenance is ~2000, so a 400 kcal deficit means 1600.
    result = _evaluate(_window(), _weights(80, 0))
    assert result.status == "adjust"
    assert result.observed_maintenance == 2000
    assert result.actual_weekly_change_kg == 0
    assert result.expected_weekly_change_kg == pytest.approx(-0.36, abs=0.01)
    assert 1700 <= result.recommended_goal <= 1800  # shrunk and capped at -300
    assert result.change < 0


def test_losing_faster_than_planned_suggests_higher_goal():
    result = _evaluate(_window(), _weights(80, -1.0))
    assert result.status == "adjust"
    assert result.observed_maintenance == 3100
    assert result.recommended_goal == 2300


def test_on_plan_is_on_track():
    # 2000 intake, -0.36 kg/week -> maintenance ~2400 = 2000 + 400.
    result = _evaluate(_window(), _weights(80, -0.36))
    assert result.status == "on_track"
    assert result.recommended_goal is None


def test_never_suggests_below_floor():
    result = _evaluate(_window(calories=1600, goal=1600), _weights(80, 0), current_goal=1600)
    assert result.status == "adjust"
    assert result.recommended_goal == 1500
    assert result.capped_at_minimum


def test_cooldown_after_review():
    now = dt.datetime(2026, 11, 20, tzinfo=dt.UTC)
    result = _evaluate(_window(), _weights(80, 0), reviewed_at=now - dt.timedelta(days=3), now=now)
    assert result.status == "recently_reviewed"


def test_cooldown_wins_over_on_track_and_low_adherence():
    # Just after a change the window is still logged against the old target:
    # calling it "on track" or "off target" would both be wrong.
    now = dt.datetime(2026, 11, 20, tzinfo=dt.UTC)
    recent = now - dt.timedelta(days=1)
    on_plan = _evaluate(_window(), _weights(80, -0.36), reviewed_at=recent, now=now)
    off_target = _evaluate(_window(calories=2600), _weights(80, 0.4), reviewed_at=recent, now=now)
    assert on_plan.status == off_target.status == "recently_reviewed"
    later = _evaluate(_window(), _weights(80, -0.36), reviewed_at=now - dt.timedelta(days=15), now=now)
    assert later.status == "on_track"


async def _headers(client: AsyncClient, token: str) -> tuple[dict, int]:
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.get("/api/v1/users/me", headers=headers)
    return headers, response.json()["id"]


@pytest.mark.asyncio
async def test_weight_log_edit_delete_and_current_weight(client: AsyncClient, db_session: AsyncSession):
    headers, user_id = await _headers(client, "mock_token_weight_crud")
    today = dt.date.today()
    old = today - dt.timedelta(days=5)

    first = await client.post("/api/v1/weight/entries", json={"weight_kg": 82.4, "date": old.isoformat()}, headers=headers)
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/weight/entries", json={"weight_kg": 81.0}, headers=headers)
    assert second.status_code == 201
    assert (await client.get("/api/v1/users/me", headers=headers)).json()["weight_kg"] == 81.0

    # Same day replaces.
    again = await client.post("/api/v1/weight/entries", json={"weight_kg": 80.8}, headers=headers)
    assert again.json()["id"] == second.json()["id"]

    overview = (await client.get("/api/v1/weight", headers=headers)).json()
    assert [e["weight_kg"] for e in overview["entries"]] == [82.4, 80.8]
    assert overview["latest_kg"] == 80.8
    assert overview["entries"][0]["trend_kg"] == 82.4
    assert overview["adaptive"]["status"] == "not_enough_weights"

    patched = await client.patch(f"/api/v1/weight/entries/{second.json()['id']}", json={"weight_kg": 80.5}, headers=headers)
    assert patched.status_code == 200
    assert (await client.get("/api/v1/users/me", headers=headers)).json()["weight_kg"] == 80.5

    removed = await client.delete(f"/api/v1/weight/entries/{second.json()['id']}", headers=headers)
    assert removed.status_code == 204
    assert (await client.get("/api/v1/users/me", headers=headers)).json()["weight_kg"] == 82.4

    last = await client.delete(f"/api/v1/weight/entries/{first.json()['id']}", headers=headers)
    assert last.status_code == 409

    future = await client.post(
        "/api/v1/weight/entries",
        json={"weight_kg": 80, "date": (today + dt.timedelta(days=1)).isoformat()},
        headers=headers,
    )
    assert future.status_code == 422


@pytest.mark.asyncio
async def test_profile_weight_edit_adds_history_entry(client: AsyncClient):
    headers, _ = await _headers(client, "mock_token_weight_profile")
    response = await client.patch("/api/v1/users/me", json={"weight_kg": 77.5}, headers=headers)
    assert response.status_code == 200, response.text
    overview = (await client.get("/api/v1/weight", headers=headers)).json()
    assert [(e["weight_kg"], e["source"]) for e in overview["entries"]] == [(77.5, "profile")]


@pytest.mark.asyncio
async def test_other_users_entries_are_hidden(client: AsyncClient):
    owner, _ = await _headers(client, "mock_token_weight_owner")
    other, _ = await _headers(client, "mock_token_weight_other")
    entry = (await client.post("/api/v1/weight/entries", json={"weight_kg": 70}, headers=owner)).json()
    assert (await client.patch(f"/api/v1/weight/entries/{entry['id']}", json={"weight_kg": 71}, headers=other)).status_code == 404
    assert (await client.delete(f"/api/v1/weight/entries/{entry['id']}", headers=other)).status_code == 404


@pytest.mark.asyncio
async def test_adaptive_target_apply_and_dismiss(client: AsyncClient, db_session: AsyncSession):
    headers, user_id = await _headers(client, "mock_token_weight_adaptive")
    user = (await db_session.execute(select(User).where(User.id == user_id))).scalar_one()
    user.daily_calorie_goal = 2000
    user.goal_type = "lose"
    user.target_pace = "balanced"
    user.sex = "male"
    today = dt.date.today()
    # 28 days eaten exactly on target, weight flat: the deficit is not real.
    db_session.add_all(
        DailySummary(
            user_id=user_id,
            date=today - dt.timedelta(days=offset),
            consumed_calories=2000,
            burned_calories=0,
            remaining_calories=0,
        )
        for offset in range(1, 29)
    )
    await db_session.flush()
    for offset in range(28, -1, -4):
        response = await client.post(
            "/api/v1/weight/entries",
            json={"weight_kg": 80.0, "date": (today - dt.timedelta(days=offset)).isoformat()},
            headers=headers,
        )
        assert response.status_code == 201

    adaptive = (await client.get("/api/v1/weight", headers=headers)).json()["adaptive"]
    assert adaptive["status"] == "adjust", adaptive
    recommended = adaptive["recommended_goal"]
    assert recommended < 2000

    stale = await client.post("/api/v1/weight/adaptive-target/apply", json={"recommended_goal": recommended + 50}, headers=headers)
    assert stale.status_code == 409

    applied = await client.post("/api/v1/weight/adaptive-target/apply", json={"recommended_goal": recommended}, headers=headers)
    assert applied.status_code == 200, applied.text
    me = (await client.get("/api/v1/users/me", headers=headers)).json()
    assert me["daily_calorie_goal"] == recommended
    assert me["calorie_target_source"] == "adaptive"
    assert applied.json()["status"] == "recently_reviewed"

    dismissed = await client.post("/api/v1/weight/adaptive-target/dismiss", headers=headers)
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "recently_reviewed"
