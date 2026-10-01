"""Any past day stays editable: activities can be logged onto, edited and
removed from a past day, and a meal can be moved onto one. Each change
resyncs the affected day's summary."""
import datetime as dt

import pytest
from httpx import AsyncClient

from app.models.meal import Meal, MealItem


async def _user(client: AsyncClient, token: str) -> tuple[dict, int]:
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.get("/api/v1/users/me", headers=headers)
    return headers, response.json()["id"]


async def _summary(client: AsyncClient, headers: dict, day: dt.date) -> dict:
    response = await client.get(f"/api/v1/summary/date/{day.isoformat()}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_activity_can_be_logged_edited_and_removed_on_a_past_day(client: AsyncClient):
    headers, _ = await _user(client, "mock_token_past_activity")
    day = dt.date.today() - dt.timedelta(days=3)

    created = await client.post(
        "/api/v1/burned-calories",
        json={"activity_name": "Corsa", "calories": 300, "date": day.isoformat()},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    entry = created.json()
    assert dt.datetime.fromisoformat(entry["created_at"]).date() == day
    assert (await _summary(client, headers, day))["burned_calories"] == 300

    listed = await client.get(f"/api/v1/burned-calories?date={day.isoformat()}", headers=headers)
    assert [e["id"] for e in listed.json()] == [entry["id"]]

    edited = await client.patch(
        f"/api/v1/burned-calories/{entry['id']}", json={"calories": 450}, headers=headers
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["activity_name"] == "Corsa"
    assert (await _summary(client, headers, day))["burned_calories"] == 450

    removed = await client.delete(f"/api/v1/burned-calories/{entry['id']}", headers=headers)
    assert removed.status_code == 204
    assert (await _summary(client, headers, day))["burned_calories"] == 0


@pytest.mark.asyncio
async def test_activity_cannot_be_logged_in_the_future(client: AsyncClient):
    headers, _ = await _user(client, "mock_token_future_activity")
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    response = await client.post(
        "/api/v1/burned-calories",
        json={"calories": 100, "date": tomorrow.isoformat()},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_someone_elses_activity_is_not_editable(client: AsyncClient):
    owner, _ = await _user(client, "mock_token_activity_owner")
    other, _ = await _user(client, "mock_token_activity_other")
    entry = (await client.post("/api/v1/burned-calories", json={"calories": 120}, headers=owner)).json()
    assert (await client.patch(f"/api/v1/burned-calories/{entry['id']}", json={"calories": 1}, headers=other)).status_code == 404
    assert (await client.delete(f"/api/v1/burned-calories/{entry['id']}", headers=other)).status_code == 404


@pytest.mark.asyncio
async def test_meal_moves_onto_a_past_day_and_both_summaries_follow(client: AsyncClient, db_session):
    headers, user_id = await _user(client, "mock_token_move_meal")
    today = dt.date.today()
    day = today - dt.timedelta(days=2)
    meal = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Pasta",
        meal_name="Pasta",
        estimated_calories=500,
        confirmed_calories=500,
        items=[MealItem(name="Pasta", weight_grams=200, calories_per_100g=250.0)],
    )
    db_session.add(meal)
    await db_session.commit()
    await db_session.refresh(meal)
    original_time = meal.created_at.time()

    response = await client.patch(
        f"/api/v1/meals/{meal.id}",
        json={"logged_date": day.isoformat(), "is_confirmed": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    moved_at = dt.datetime.fromisoformat(response.json()["created_at"])
    assert moved_at.date() == day
    assert moved_at.time().replace(microsecond=0) == original_time.replace(microsecond=0)

    day_meals = await client.get(f"/api/v1/meals/date/{day.isoformat()}", headers=headers)
    assert [m["id"] for m in day_meals.json()] == [meal.id]
    assert (await _summary(client, headers, day))["consumed_calories"] > 0
    assert (await _summary(client, headers, today))["consumed_calories"] == 0


@pytest.mark.asyncio
async def test_meal_cannot_move_into_the_future(client: AsyncClient, db_session):
    headers, user_id = await _user(client, "mock_token_future_meal")
    meal = Meal(user_id=user_id, source_type="text", original_input="Mela", meal_name="Mela", estimated_calories=80)
    db_session.add(meal)
    await db_session.commit()
    await db_session.refresh(meal)
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    response = await client.patch(
        f"/api/v1/meals/{meal.id}", json={"logged_date": tomorrow.isoformat()}, headers=headers
    )
    assert response.status_code == 422
