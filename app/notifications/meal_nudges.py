"""Meal pushes: first-meal nudge (A1), meal-window reminder (A2), analysis ready (B4).

All three sit on the shared dispatcher, so they share the daily/weekly cap,
quiet hours and the per-category switch (``meal_reminder``). The rules here
decide only *whether* a person is due one, never how it is delivered.

Tone rules (product vision): a reminder asks, it never scolds. Nothing here
mentions a missed day, a streak or being over target.
"""

import datetime as dt
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.insight import InsightNotificationPreference, NotificationDelivery
from app.models.meal import Meal
from app.models.meal_analysis import MealAnalysisJob
from app.models.push_device import PushDevice
from app.models.user import User
from app.notifications.copy import push_text
from app.notifications.dispatcher import (
    CATEGORY_MEAL_REMINDER,
    CATEGORY_MEAL_STATUS,
    PRIORITY_NORMAL,
    PRIORITY_TRANSACTIONAL,
    PushDispatcher,
    TypePolicy,
    user_zone,
)

logger = logging.getLogger("app.notifications.meal_nudges")

TYPE_FIRST_MEAL = "first_meal_nudge"
TYPE_MEAL_REMINDER = "meal_reminder"
TYPE_ANALYSIS_READY = "analysis_ready"

# A reminder that quiet hours or a retry pushed this far is no longer about
# the meal it asked about.
REMINDER_TTL = dt.timedelta(minutes=90)
# How long after its due time a sweep may still create a nudge.
DUE_WINDOW = dt.timedelta(minutes=30)
FIRST_MEAL_PERIOD = dt.timedelta(hours=72)
FIRST_MEAL_MAX = 2
# No nudge right after onboarding: the person has just been in the app.
FIRST_MEAL_GRACE = dt.timedelta(minutes=90)
HISTORY_DAYS = 28
HABIT_DAYS = 14
HABIT_MIN_DAYS = 3
NEW_USER_MAX_DAYS = 5
RECENT_ACTIVITY = dt.timedelta(days=7)
# A reminder counts as ignored when it was not opened and no meal followed.
IGNORE_WINDOW = dt.timedelta(hours=2)
BACKOFF_AFTER = 3
PAUSE_AFTER = 7
ANALYSIS_READY_TTL = dt.timedelta(hours=6)


@dataclass(frozen=True)
class MealSlot:
    name: str
    band_start: dt.time
    band_end: dt.time
    first_meal_at: dt.time
    default_reminder_at: dt.time
    latest_reminder_at: dt.time

    def contains(self, value: dt.time) -> bool:
        return self.band_start <= value < self.band_end


LUNCH = MealSlot(
    "lunch",
    band_start=dt.time(10, 30),
    band_end=dt.time(16, 0),
    first_meal_at=dt.time(12, 45),
    default_reminder_at=dt.time(13, 30),
    latest_reminder_at=dt.time(15, 30),
)
DINNER = MealSlot(
    "dinner",
    band_start=dt.time(16, 30),
    band_end=dt.time(23, 0),
    first_meal_at=dt.time(19, 45),
    default_reminder_at=dt.time(20, 30),
    latest_reminder_at=dt.time(21, 30),
)
SLOTS = (LUNCH, DINNER)
_SLOTS_BY_NAME = {slot.name: slot for slot in SLOTS}


def _aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def _at(date: dt.date, time: dt.time, zone: ZoneInfo) -> dt.datetime:
    return dt.datetime.combine(date, time, tzinfo=zone)


def _is_due(local_now: dt.datetime, due: dt.time) -> bool:
    start = _at(local_now.date(), due, local_now.tzinfo)  # type: ignore[arg-type]
    return start <= local_now < start + DUE_WINDOW


def reminder_time(slot: MealSlot, local_meals: list[dt.datetime], *, today: dt.date) -> dt.time | None:
    """When to ask about ``slot``, learned from the person's own meals.

    A slot counts as a habit when it was logged on at least HABIT_MIN_DAYS of
    the last HABIT_DAYS days: then the reminder comes ~45 min after the usual
    first meal in it. People with little history get the default time; people
    with history who never log this slot are never asked about it.
    """
    firsts: dict[dt.date, dt.time] = {}
    for moment in local_meals:
        clock = moment.time().replace(tzinfo=None)
        if moment.date() >= today or not slot.contains(clock):
            continue
        firsts[moment.date()] = min(firsts.get(moment.date(), clock), clock)
    recent = [clock for day, clock in firsts.items() if (today - day).days <= HABIT_DAYS]
    if len(recent) >= HABIT_MIN_DAYS:
        minutes = statistics.median(clock.hour * 60 + clock.minute for clock in recent) + 45
        earliest = slot.band_start.hour * 60 + slot.band_start.minute + 60
        latest = slot.latest_reminder_at.hour * 60 + slot.latest_reminder_at.minute
        minutes = int(min(max(minutes, earliest), latest))
        # Round to the sweep's 5-minute grid so the due window is stable.
        minutes -= minutes % 5
        return dt.time(minutes // 60, minutes % 60)
    logged_days = {moment.date() for moment in local_meals if moment.date() < today}
    if len(logged_days) < NEW_USER_MAX_DAYS:
        return slot.default_reminder_at
    return None


def _push_data(notification_type: str, **extra: str) -> dict[str, str]:
    return {"type": notification_type, "route": "/", **extra}


async def _meal_since(db: AsyncSession, user_id: int, since: dt.datetime) -> bool:
    return bool(
        await db.scalar(
            select(Meal.id).where(Meal.user_id == user_id, Meal.created_at >= since).limit(1)
        )
    )


class _ReminderPolicy(TypePolicy):
    def __init__(self, db: AsyncSession):
        self.db = db

    async def preference_reason(self, delivery, preference) -> str | None:
        return None if settings.MEAL_NUDGES_ENABLED else "type_disabled"

    async def can_revive(self, delivery: NotificationDelivery) -> bool:
        created = _aware(delivery.created_at)
        return dt.datetime.now(dt.UTC) - created < REMINDER_TTL

    def _stale(self, delivery: NotificationDelivery, now: dt.datetime) -> bool:
        return now - _aware(delivery.created_at) > REMINDER_TTL


class FirstMealPolicy(_ReminderPolicy):
    notification_type = TYPE_FIRST_MEAL

    async def state_reason(self, delivery, user, *, now) -> str | None:
        if await _meal_since(self.db, user.id, dt.datetime(1970, 1, 1, tzinfo=dt.UTC)):
            return "meal_logged"
        return "stale" if self._stale(delivery, now) else None


class MealReminderPolicy(_ReminderPolicy):
    notification_type = TYPE_MEAL_REMINDER

    async def state_reason(self, delivery, user, *, now) -> str | None:
        meta = delivery.metadata_json or {}
        slot = _SLOTS_BY_NAME.get(meta.get("slot", ""))
        zone = user_zone(user, await self.db.get(InsightNotificationPreference, user.id))
        if slot is not None:
            local_now = now.astimezone(zone)
            band_start = _at(local_now.date(), slot.band_start, zone).astimezone(dt.UTC)
            if await _meal_since(self.db, user.id, band_start):
                return "meal_logged"
        return "stale" if self._stale(delivery, now) else None


class AnalysisReadyPolicy(TypePolicy):
    """B4: a photo finished analysing and the person has not looked at it yet."""

    notification_type = TYPE_ANALYSIS_READY

    def __init__(self, db: AsyncSession):
        self.db = db

    async def preference_reason(self, delivery, preference) -> str | None:
        return None if settings.MEAL_NUDGES_ENABLED else "type_disabled"

    async def can_revive(self, delivery: NotificationDelivery) -> bool:
        return False

    async def state_reason(self, delivery, user, *, now) -> str | None:
        meal_id = (delivery.metadata_json or {}).get("meal_id")
        meal = await self.db.get(Meal, meal_id) if isinstance(meal_id, int) else None
        if meal is None:
            return "source_missing"
        if meal.confirmed_at is not None or meal.confirmed_calories is not None:
            return "already_confirmed"
        if now - _aware(delivery.created_at) > ANALYSIS_READY_TTL:
            return "stale"
        return None


# -- B4 --------------------------------------------------------------------


async def schedule_analysis_ready(
    db: AsyncSession,
    job: MealAnalysisJob,
    meal: Meal,
    *,
    now: dt.datetime | None = None,
) -> NotificationDelivery | None:
    """Queue the "analysis ready" push; it fires only if the meal is still unconfirmed."""
    if not (settings.PUSH_NOTIFICATIONS_ENABLED and settings.MEAL_NUDGES_ENABLED):
        return None
    now = now or dt.datetime.now(dt.UTC)
    user = await db.get(User, job.user_id)
    if user is None or not await db.scalar(
        select(PushDevice.id).where(PushDevice.user_id == user.id).limit(1)
    ):
        return None
    label = (meal.meal_name or "").strip() or push_text(user.locale, f"meal.{meal.meal_category}")
    if len(label) > 80:
        label = label[:79].rstrip() + "…"
    from app.notifications.dispatcher import build_dispatcher

    return await build_dispatcher(db).enqueue(
        user,
        notification_type=TYPE_ANALYSIS_READY,
        category=CATEGORY_MEAL_STATUS,
        priority=PRIORITY_TRANSACTIONAL,
        title=push_text(user.locale, "analysis_ready.title"),
        body=push_text(
            user.locale,
            "analysis_ready.body",
            meal=label,
            kcal=meal.estimated_calories,
        ),
        data=_push_data(TYPE_ANALYSIS_READY, meal_id=str(meal.id)),
        metadata={"meal_id": meal.id, "job_id": job.id},
        idempotency_key=f"{TYPE_ANALYSIS_READY}:{job.id}",
        not_before=now + dt.timedelta(seconds=settings.ANALYSIS_READY_PUSH_DELAY_SECONDS),
        now=now,
    )


# -- A1 / A2 planner ---------------------------------------------------------


class MealNudgePlanner:
    """Run by a beat sweep: creates the first-meal and meal-window pushes due now."""

    def __init__(self, db: AsyncSession, dispatcher: PushDispatcher):
        self.db = db
        self.dispatcher = dispatcher

    async def _candidates(self) -> list[User]:
        reachable = select(PushDevice.user_id).distinct()
        return list(
            (
                await self.db.scalars(
                    select(User).where(
                        User.id.in_(reachable),
                        User.onboarding_status == "completed",
                        User.access_status == "active",
                        User.deletion_in_progress.is_(False),
                    )
                )
            ).all()
        )

    async def _meal_times(self, user_ids: list[int], *, now: dt.datetime) -> dict[int, list[dt.datetime]]:
        rows = await self.db.execute(
            select(Meal.user_id, Meal.created_at).where(
                Meal.user_id.in_(user_ids),
                Meal.created_at >= now - dt.timedelta(days=HISTORY_DAYS),
            )
        )
        times: dict[int, list[dt.datetime]] = defaultdict(list)
        for user_id, created_at in rows:
            times[user_id].append(_aware(created_at))
        return times

    async def _ever_logged(self, user_ids: list[int]) -> set[int]:
        rows = await self.db.scalars(select(Meal.user_id).where(Meal.user_id.in_(user_ids)).distinct())
        return set(rows.all())

    async def run(self, *, now: dt.datetime | None = None) -> dict[str, int]:
        if not (settings.PUSH_NOTIFICATIONS_ENABLED and settings.MEAL_NUDGES_ENABLED):
            return {"first_meal": 0, "meal_reminder": 0}
        now = now or dt.datetime.now(dt.UTC)
        users = await self._candidates()
        if not users:
            return {"first_meal": 0, "meal_reminder": 0}
        ids = [user.id for user in users]
        history = await self._meal_times(ids, now=now)
        ever = await self._ever_logged(ids)
        counts = {"first_meal": 0, "meal_reminder": 0}
        for user_id in ids:
            # Reloaded each time: a rollback after one person's failure must
            # not leave the next person's row expired.
            user = await self.db.get(User, user_id, populate_existing=True)
            if user is None:
                continue
            preference = await self.db.get(InsightNotificationPreference, user.id)
            if preference is not None and not self.dispatcher.category_enabled(
                preference, CATEGORY_MEAL_REMINDER
            ):
                continue
            zone = user_zone(user, preference)
            local_now = now.astimezone(zone)
            try:
                if user.id not in ever:
                    counts["first_meal"] += await self._first_meal(user, local_now, now=now)
                else:
                    counts["meal_reminder"] += await self._meal_reminder(
                        user, local_now, history.get(user.id, []), now=now
                    )
            except Exception:
                logger.exception("event=meal_nudge_plan_failed user_id=%s", user.id)
                await self.db.rollback()
        return counts

    async def _first_meal(self, user: User, local_now: dt.datetime, *, now: dt.datetime) -> int:
        completed = user.onboarding_completed_at
        if completed is None:
            return 0
        completed = _aware(completed)
        if not FIRST_MEAL_GRACE <= now - completed <= FIRST_MEAL_PERIOD:
            return 0
        slot = next((item for item in SLOTS if _is_due(local_now, item.first_meal_at)), None)
        if slot is None:
            return 0
        key = f"{TYPE_FIRST_MEAL}:{user.id}:{local_now.date().isoformat()}:{slot.name}"
        if await self._exists(key):
            return 0
        sent = await self.db.scalar(
            select(func.count(NotificationDelivery.id)).where(
                NotificationDelivery.user_id == user.id,
                NotificationDelivery.notification_type == TYPE_FIRST_MEAL,
                NotificationDelivery.status.in_(("scheduled", "sending", "sent", "failed")),
            )
        )
        if int(sent or 0) >= FIRST_MEAL_MAX:
            return 0
        await self.dispatcher.enqueue(
            user,
            notification_type=TYPE_FIRST_MEAL,
            category=CATEGORY_MEAL_REMINDER,
            priority=PRIORITY_NORMAL,
            title=push_text(user.locale, f"first_meal.{slot.name}.title"),
            body=push_text(user.locale, "first_meal.body"),
            data=_push_data(TYPE_FIRST_MEAL, slot=slot.name),
            metadata={"slot": slot.name, "local_date": local_now.date().isoformat()},
            idempotency_key=key,
            now=now,
        )
        await self.db.commit()
        return 1

    async def _meal_reminder(
        self,
        user: User,
        local_now: dt.datetime,
        meals: list[dt.datetime],
        *,
        now: dt.datetime,
    ) -> int:
        if not meals or now - max(meals) > RECENT_ACTIVITY:
            return 0
        zone = local_now.tzinfo
        local_meals = [moment.astimezone(zone) for moment in meals]
        today = local_now.date()
        for slot in SLOTS:
            due = reminder_time(slot, local_meals, today=today)
            if due is None or not _is_due(local_now, due):
                continue
            if any(
                moment.date() == today and moment.time() >= slot.band_start for moment in local_meals
            ):
                return 0
            # One reminder per local day, whichever slot comes first.
            key = f"{TYPE_MEAL_REMINDER}:{user.id}:{today.isoformat()}"
            if await self._exists(key):
                return 0
            ignored = await self.ignored_streak(user.id)
            if ignored >= PAUSE_AFTER:
                return 0
            if ignored >= BACKOFF_AFTER and today.toordinal() % 2:
                return 0
            await self.dispatcher.enqueue(
                user,
                notification_type=TYPE_MEAL_REMINDER,
                category=CATEGORY_MEAL_REMINDER,
                priority=PRIORITY_NORMAL,
                title=push_text(user.locale, f"meal_reminder.{slot.name}.title"),
                body=push_text(user.locale, "meal_reminder.body"),
                data=_push_data(TYPE_MEAL_REMINDER, slot=slot.name),
                metadata={"slot": slot.name, "local_date": today.isoformat(), "ignored_streak": ignored},
                idempotency_key=key,
                now=now,
            )
            await self.db.commit()
            return 1
        return 0

    async def _exists(self, key: str) -> bool:
        return bool(
            await self.db.scalar(
                select(NotificationDelivery.id).where(NotificationDelivery.idempotency_key == key)
            )
        )

    async def ignored_streak(self, user_id: int) -> int:
        """Consecutive most-recent meal reminders that were neither opened nor followed by a meal."""
        sent = list(
            (
                await self.db.scalars(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.user_id == user_id,
                        NotificationDelivery.notification_type == TYPE_MEAL_REMINDER,
                        NotificationDelivery.status == "sent",
                    )
                    .order_by(desc(NotificationDelivery.sent_at))
                    .limit(PAUSE_AFTER)
                )
            ).all()
        )
        streak = 0
        for delivery in sent:
            if delivery.opened_at is not None or delivery.sent_at is None:
                break
            sent_at = _aware(delivery.sent_at)
            followed = await self.db.scalar(
                select(Meal.id)
                .where(
                    Meal.user_id == user_id,
                    Meal.created_at >= sent_at,
                    Meal.created_at < sent_at + IGNORE_WINDOW,
                )
                .limit(1)
            )
            if followed:
                break
            streak += 1
        return streak
