"""Diary days follow the user's timezone (X-Timezone), never the server clock."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_time import local_date, moved_to_day, parse_timezone, use_timezone
from app.models.meal import Meal, MealItem
from app.models.user import User

ROME = ZoneInfo("Europe/Rome")
# UTC+14 and UTC-11: for most of any UTC day these sit on different dates,
# so a server-clock "today" cannot accidentally match both.
KIRITIMATI = "Pacific/Kiritimati"
PAGO_PAGO = "Pacific/Pago_Pago"


def _headers(token: str, zone: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if zone is not None:
        headers["X-Timezone"] = zone
    return headers


def _today_in(zone: str) -> dt.date:
    return dt.datetime.now(dt.UTC).astimezone(ZoneInfo(zone)).date()


async def _add_meal(db: AsyncSession, user_id: int, created_at: dt.datetime, zone: ZoneInfo) -> Meal:
    with use_timezone(zone):
        meal = Meal(
            user_id=user_id,
            source_type="text",
            original_input="Pizza",
            meal_name="Pizza",
            estimated_calories=800,
            created_at=created_at,
        )
        db.add(meal)
        await db.flush()
        db.add(MealItem(meal_id=meal.id, name="Pizza", weight_grams=300, calories_per_100g=266))
        await db.commit()
    return meal


def test_local_date_uses_the_user_zone() -> None:
    late_utc = dt.datetime(2026, 3, 1, 23, 30, tzinfo=dt.UTC)
    assert local_date(late_utc, ROME) == dt.date(2026, 3, 2)
    assert local_date(late_utc, ZoneInfo("America/New_York")) == dt.date(2026, 3, 1)
    # SQLite hands back naive UTC values.
    assert local_date(late_utc.replace(tzinfo=None), ROME) == dt.date(2026, 3, 2)


def test_moving_a_meal_keeps_its_wall_clock_time_across_dst() -> None:
    # 20:30 in Rome on 30 March (CEST, UTC+2) moved to 28 March (CET, UTC+1).
    dinner = dt.datetime(2026, 3, 30, 18, 30, tzinfo=dt.UTC)
    moved = moved_to_day(dinner, dt.date(2026, 3, 28), ROME)
    assert moved == dt.datetime(2026, 3, 28, 19, 30, tzinfo=dt.UTC)
    assert moved.astimezone(ROME).time() == dt.time(20, 30)


def test_only_real_iana_zones_are_accepted() -> None:
    assert parse_timezone("Europe/Rome") == ROME
    for value in (None, "", "CET+1", "../etc/passwd", "Mars/Olympus", "x" * 80):
        assert parse_timezone(value) is None, value


@pytest.mark.asyncio
async def test_today_follows_the_header_zone(client: AsyncClient) -> None:
    for zone in (KIRITIMATI, PAGO_PAGO):
        response = await client.get("/api/v1/summary/today", headers=_headers("mock_token_tz_today", zone))
        assert response.status_code == 200, response.text
        assert response.json()["date"] == _today_in(zone).isoformat(), zone


@pytest.mark.asyncio
async def test_reported_zone_is_remembered_for_later_requests(client: AsyncClient, db_session: AsyncSession) -> None:
    await client.get("/api/v1/users/me", headers=_headers("mock_token_tz_memory", KIRITIMATI))

    # No header (or a bogus one): the stored zone still decides "today".
    for headers in (_headers("mock_token_tz_memory"), _headers("mock_token_tz_memory", "Not/AZone")):
        response = await client.get("/api/v1/summary/today", headers=headers)
        assert response.json()["date"] == _today_in(KIRITIMATI).isoformat()

    user = (await db_session.execute(User.__table__.select().where(User.firebase_uid == "tz_memory"))).one()
    assert user.timezone == KIRITIMATI


@pytest.mark.asyncio
async def test_meal_after_local_midnight_belongs_to_the_new_day(client: AsyncClient, db_session: AsyncSession) -> None:
    """The reported bug: a meal at 00:30 in Rome is still yesterday in UTC."""
    headers = _headers("mock_token_tz_midnight", "Europe/Rome")
    user_id = (await client.get("/api/v1/users/me", headers=headers)).json()["id"]

    utc_today = dt.datetime.now(dt.UTC).date()
    just_after_rome_midnight = dt.datetime.combine(utc_today, dt.time(0, 30), tzinfo=ROME).astimezone(dt.UTC)
    assert just_after_rome_midnight.date() == utc_today - dt.timedelta(days=1)
    meal = await _add_meal(db_session, user_id, just_after_rome_midnight, ROME)
    assert meal.local_date == utc_today

    on_rome_day = await client.get(f"/api/v1/meals/date/{utc_today.isoformat()}", headers=headers)
    assert [m["id"] for m in on_rome_day.json()] == [meal.id]
    assert on_rome_day.json()[0]["local_date"] == utc_today.isoformat()

    previous_day = (utc_today - dt.timedelta(days=1)).isoformat()
    on_utc_day = await client.get(f"/api/v1/meals/date/{previous_day}", headers=headers)
    assert on_utc_day.json() == []

    summary = await client.get(f"/api/v1/summary/date/{utc_today.isoformat()}", headers=headers)
    assert summary.json()["consumed_calories"] == 800


@pytest.mark.asyncio
async def test_meal_day_is_fixed_when_logged_even_after_travelling(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = _headers("mock_token_tz_travel", "Europe/Rome")
    user_id = (await client.get("/api/v1/users/me", headers=headers)).json()["id"]
    day = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=2)
    late_dinner = dt.datetime.combine(day, dt.time(23, 30), tzinfo=ROME).astimezone(dt.UTC)
    meal = await _add_meal(db_session, user_id, late_dinner, ROME)

    # Now in New York, where that instant was still 17:30 on the same day,
    # and in Tokyo, where it was already the next day: the diary keeps it.
    for zone in ("America/New_York", "Asia/Tokyo"):
        response = await client.get(f"/api/v1/meals/date/{day.isoformat()}", headers=_headers("mock_token_tz_travel", zone))
        assert [m["id"] for m in response.json()] == [meal.id], zone


@pytest.mark.asyncio
async def test_moving_a_meal_to_another_day_uses_the_user_calendar(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = _headers("mock_token_tz_move", "Europe/Rome")
    user_id = (await client.get("/api/v1/users/me", headers=headers)).json()["id"]
    today = _today_in("Europe/Rome")
    lunch = dt.datetime.combine(today, dt.time(13, 0), tzinfo=ROME).astimezone(dt.UTC)
    meal = await _add_meal(db_session, user_id, lunch, ROME)
    yesterday = today - dt.timedelta(days=1)

    response = await client.patch(
        f"/api/v1/meals/{meal.id}", json={"logged_date": yesterday.isoformat()}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["local_date"] == yesterday.isoformat()
    moved = dt.datetime.fromisoformat(body["created_at"])
    moved = moved if moved.tzinfo else moved.replace(tzinfo=dt.UTC)
    assert moved.astimezone(ROME).time() == dt.time(13, 0)

    assert (await client.get(f"/api/v1/meals/date/{today.isoformat()}", headers=headers)).json() == []
    assert len((await client.get(f"/api/v1/meals/date/{yesterday.isoformat()}", headers=headers)).json()) == 1


@pytest.mark.asyncio
async def test_activity_logged_today_and_on_a_past_day_use_the_user_calendar(client: AsyncClient) -> None:
    headers = _headers("mock_token_tz_activity", KIRITIMATI)
    today = _today_in(KIRITIMATI)

    created = await client.post("/api/v1/burned-calories", json={"calories": 200}, headers=headers)
    assert created.json()["local_date"] == today.isoformat()

    past = today - dt.timedelta(days=2)
    created = await client.post(
        "/api/v1/burned-calories", json={"calories": 300, "date": past.isoformat()}, headers=headers
    )
    assert created.json()["local_date"] == past.isoformat()
    listed = await client.get(f"/api/v1/burned-calories?date={past.isoformat()}", headers=headers)
    assert [e["calories"] for e in listed.json()] == [300]

    future = today + dt.timedelta(days=1)
    rejected = await client.post(
        "/api/v1/burned-calories", json={"calories": 100, "date": future.isoformat()}, headers=headers
    )
    assert rejected.status_code == 422


def test_default_meal_category_reads_the_user_clock() -> None:
    from app.api.v1.routes.meals import _time_based_meal_category

    # 20:00 in Rome is 18:00 UTC: dinner for the user, not a snack.
    with use_timezone("Europe/Rome"):
        evening = dt.datetime(2026, 7, 1, 18, 0, tzinfo=dt.UTC).astimezone(ROME)
        assert _time_based_meal_category(evening) == "dinner"
