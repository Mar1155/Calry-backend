import datetime as dt
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insight import InsightNotificationPreference, NotificationDelivery
from app.models.meal import Meal
from app.models.meal_analysis import MealAnalysisJob
from app.models.user import User
from app.notifications import copy
from app.notifications.devices import PushDeviceService
from app.notifications.dispatcher import PRIORITY_TRANSACTIONAL, build_dispatcher
from app.notifications.meal_nudges import (
    DINNER,
    LUNCH,
    TYPE_ANALYSIS_READY,
    TYPE_FIRST_MEAL,
    TYPE_MEAL_REMINDER,
    MealNudgePlanner,
    reminder_time,
    schedule_analysis_ready,
)

ROME = ZoneInfo("Europe/Rome")


class RecordingGateway:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, *, token: str, title: str, body: str, data: dict[str, str]) -> str:
        self.sent.append({"token": token, "title": title, "body": body, "data": data})
        return f"fcm-{len(self.sent)}"


def rome(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=ROME).astimezone(dt.UTC)


async def _user(
    db: AsyncSession,
    *,
    onboarded_at: dt.datetime | None,
    locale: str = "it",
) -> User:
    token = uuid4().hex
    user = User(
        firebase_uid=f"nudge-{token}",
        email=f"nudge-{token}@example.com",
        daily_calorie_goal=2000,
        timezone="Europe/Rome",
        locale=locale,
        onboarding_status="completed" if onboarded_at else "in_progress",
        onboarding_completed_at=onboarded_at,
    )
    db.add(user)
    await db.flush()
    await PushDeviceService(db).register(user, f"nudge-device-{token}")
    return user


async def _meal(db: AsyncSession, user: User, at: dt.datetime, *, kcal: int = 600) -> Meal:
    meal = Meal(
        user_id=user.id,
        source_type="photo",
        original_input="pasta",
        meal_name="Pasta al pomodoro",
        meal_category="lunch",
        estimated_calories=kcal,
        created_at=at,
        local_date=at.astimezone(ROME).date(),
    )
    db.add(meal)
    await db.flush()
    return meal


async def _deliveries(db: AsyncSession, user: User, notification_type: str) -> list[NotificationDelivery]:
    return list(
        (
            await db.scalars(
                select(NotificationDelivery)
                .where(
                    NotificationDelivery.user_id == user.id,
                    NotificationDelivery.notification_type == notification_type,
                )
                .order_by(NotificationDelivery.id)
            )
        ).all()
    )


def _planner(db: AsyncSession, gateway: RecordingGateway | None = None) -> MealNudgePlanner:
    return MealNudgePlanner(db, build_dispatcher(db, gateway=gateway or RecordingGateway()))


def test_every_locale_has_every_push_string() -> None:
    keys = set(copy._COPY["en"])
    for locale in copy.SUPPORTED_LOCALES:
        assert set(copy._COPY[locale]) == keys, locale
    assert copy.push_text("pt-BR", "meal_reminder.body") == copy._COPY["en"]["meal_reminder.body"]
    assert copy.push_text("it_IT", "analysis_ready.body", meal="Pranzo", kcal=640) == (
        "Pranzo: circa 640 kcal. Tocca per controllare."
    )


def test_reminder_time_learns_the_usual_slot_and_skips_unused_ones() -> None:
    today = dt.date(2026, 11, 26)
    lunches = [dt.datetime(2026, 11, day, 12, 30, tzinfo=ROME) for day in range(19, 26)]
    assert reminder_time(LUNCH, lunches, today=today) == dt.time(13, 15)
    # Seven logged days, never a dinner: dinner is not this person's habit.
    assert reminder_time(DINNER, lunches, today=today) is None
    # Little history: default time.
    assert reminder_time(DINNER, lunches[:2], today=today) == DINNER.default_reminder_at


@pytest.mark.asyncio
async def test_first_meal_nudge_twice_at_meal_time_then_stops(db_session: AsyncSession) -> None:
    user = await _user(db_session, onboarded_at=rome(25, 9))
    planner = _planner(db_session)

    await planner.run(now=rome(25, 10))  # not a meal window
    await planner.run(now=rome(25, 12, 50))
    await planner.run(now=rome(25, 12, 55))  # same slot: no duplicate
    await planner.run(now=rome(25, 19, 50))
    await planner.run(now=rome(26, 12, 50))  # third would exceed the max

    rows = await _deliveries(db_session, user, TYPE_FIRST_MEAL)
    assert [row.metadata_json["slot"] for row in rows] == ["lunch", "dinner"]
    assert rows[0].title == "Cosa c'è a pranzo?"
    assert rows[0].category == "meal_reminder"


@pytest.mark.asyncio
async def test_first_meal_nudge_waits_after_onboarding_and_drops_once_a_meal_exists(
    db_session: AsyncSession,
) -> None:
    fresh = await _user(db_session, onboarded_at=rome(26, 12, 30))
    logged = await _user(db_session, onboarded_at=rome(25, 9))
    gateway = RecordingGateway()
    planner = _planner(db_session, gateway)

    await planner.run(now=rome(26, 12, 50))
    pending = await _deliveries(db_session, logged, TYPE_FIRST_MEAL)
    await _meal(db_session, logged, rome(26, 12, 52))
    await db_session.commit()
    result = await build_dispatcher(db_session, gateway=gateway).deliver(
        pending[0].id, now=rome(26, 12, 55)
    )

    assert await _deliveries(db_session, fresh, TYPE_FIRST_MEAL) == []
    assert result == {"status": "suppressed"}
    assert pending[0].suppression_reason == "meal_logged"
    assert gateway.sent == []


@pytest.mark.asyncio
async def test_meal_reminder_follows_the_learned_time_once_a_day(db_session: AsyncSession) -> None:
    user = await _user(db_session, onboarded_at=rome(1, 9))
    for day in range(20, 26):
        await _meal(db_session, user, rome(day, 12, 30))
    await db_session.commit()
    gateway = RecordingGateway()
    planner = _planner(db_session, gateway)

    await planner.run(now=rome(26, 13, 0))  # before 13:15
    assert await _deliveries(db_session, user, TYPE_MEAL_REMINDER) == []
    await planner.run(now=rome(26, 13, 20))
    rows = await _deliveries(db_session, user, TYPE_MEAL_REMINDER)
    result = await build_dispatcher(db_session, gateway=gateway).deliver(rows[0].id, now=rome(26, 13, 21))

    assert len(rows) == 1
    assert rows[0].title == "Pranzo fatto?"
    assert result == {"status": "sent"}
    assert gateway.sent[0]["data"]["type"] == TYPE_MEAL_REMINDER


@pytest.mark.asyncio
async def test_meal_reminder_skips_a_slot_already_logged_today(db_session: AsyncSession) -> None:
    user = await _user(db_session, onboarded_at=rome(1, 9))
    for day in range(20, 27):
        await _meal(db_session, user, rome(day, 12, 30))
    await db_session.commit()

    await _planner(db_session).run(now=rome(26, 13, 20))

    assert await _deliveries(db_session, user, TYPE_MEAL_REMINDER) == []


@pytest.mark.asyncio
async def test_meal_reminder_respects_category_opt_out(db_session: AsyncSession) -> None:
    user = await _user(db_session, onboarded_at=rome(1, 9))
    db_session.add(InsightNotificationPreference(user_id=user.id, disabled_categories=["meal_reminder"]))
    for day in range(20, 26):
        await _meal(db_session, user, rome(day, 12, 30))
    await db_session.commit()

    await _planner(db_session).run(now=rome(26, 13, 20))

    assert await _deliveries(db_session, user, TYPE_MEAL_REMINDER) == []


@pytest.mark.asyncio
async def test_ignored_reminders_halve_then_pause(db_session: AsyncSession) -> None:
    user = await _user(db_session, onboarded_at=rome(1, 9))
    await _meal(db_session, user, rome(25, 9))
    planner = _planner(db_session)

    async def ignored(count: int, first_day: int) -> None:
        for index in range(count):
            db_session.add(
                NotificationDelivery(
                    user_id=user.id,
                    notification_type=TYPE_MEAL_REMINDER,
                    category="meal_reminder",
                    idempotency_key=f"ignored-{uuid4().hex}",
                    status="sent",
                    scheduled_for=rome(first_day + index, 13),
                    sent_at=rome(first_day + index, 13),
                    metadata_json={},
                )
            )
        await db_session.flush()

    await ignored(3, 10)
    assert await planner.ignored_streak(user.id) == 3
    # A meal right after the newest reminder breaks the streak.
    await _meal(db_session, user, rome(12, 13, 30))
    assert await planner.ignored_streak(user.id) == 0
    await ignored(7, 14)
    assert await planner.ignored_streak(user.id) == 7

    # Due at the default 13:30, but seven ignored reminders pause the type.
    await planner.run(now=rome(26, 13, 35))
    assert len(await _deliveries(db_session, user, TYPE_MEAL_REMINDER)) == 10


@pytest.mark.asyncio
async def test_analysis_ready_waits_then_fires_only_if_unconfirmed(db_session: AsyncSession) -> None:
    now = rome(26, 23, 10)  # inside quiet hours: transactional still goes
    user = await _user(db_session, onboarded_at=rome(1, 9))
    pending = await _meal(db_session, user, now, kcal=640)
    confirmed = await _meal(db_session, user, now, kcal=500)
    jobs = []
    for meal in (pending, confirmed):
        job = MealAnalysisJob(user_id=user.id, meal_id=meal.id, image_url="https://x/y.jpg", status="completed")
        db_session.add(job)
        jobs.append(job)
    await db_session.flush()

    first = await schedule_analysis_ready(db_session, jobs[0], pending, now=now)
    second = await schedule_analysis_ready(db_session, jobs[1], confirmed, now=now)
    await db_session.commit()
    gateway = RecordingGateway()
    dispatcher = build_dispatcher(db_session, gateway=gateway)

    early = await dispatcher.deliver(first.id, now=now + dt.timedelta(seconds=30))
    confirmed.confirmed_at = now + dt.timedelta(seconds=60)
    await db_session.commit()
    sent = await dispatcher.deliver(first.id, now=now + dt.timedelta(minutes=3))
    skipped = await dispatcher.deliver(second.id, now=now + dt.timedelta(minutes=3))

    assert first.priority == PRIORITY_TRANSACTIONAL
    assert first.category == "meal_status"
    assert first.notification_type == TYPE_ANALYSIS_READY
    assert early == {"status": "scheduled"}
    assert sent == {"status": "sent"}
    assert gateway.sent[0]["body"] == "Pasta al pomodoro: circa 640 kcal. Tocca per controllare."
    assert gateway.sent[0]["data"]["meal_id"] == str(pending.id)
    assert skipped == {"status": "suppressed"}
    assert second.suppression_reason == "already_confirmed"


@pytest.mark.asyncio
async def test_analysis_ready_is_skipped_without_a_device(db_session: AsyncSession) -> None:
    user = User(
        firebase_uid=f"nodevice-{uuid4().hex}",
        email=f"nodevice-{uuid4().hex}@example.com",
        daily_calorie_goal=2000,
    )
    db_session.add(user)
    await db_session.flush()
    meal = await _meal(db_session, user, rome(26, 13))
    job = MealAnalysisJob(user_id=user.id, meal_id=meal.id, image_url="https://x/y.jpg")
    db_session.add(job)
    await db_session.flush()

    assert await schedule_analysis_ready(db_session, job, meal) is None
