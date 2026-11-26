import datetime as dt
import logging
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_time import UTC_ZONE, parse_timezone
from app.core.config import settings
from app.models.insight import (
    InsightNotificationPreference,
    NotificationDelivery,
    ProactiveInsight,
)
from app.models.user import User
from app.notifications.devices import PushDeviceService
from app.notifications.gateway import FirebasePushGateway, PushDeliveryError

logger = logging.getLogger("app.notifications.dispatcher")

PRIORITY_NORMAL = "normal"
PRIORITY_DIGEST = "digest"
PRIORITY_TRANSACTIONAL = "transactional"
PRIORITIES = frozenset({PRIORITY_NORMAL, PRIORITY_DIGEST, PRIORITY_TRANSACTIONAL})

CATEGORY_INSIGHT = "insight"
CATEGORY_ACCOUNT = "account"
# Follow-ups to something the person just did (a photo analysis finishing).
CATEGORY_MEAL_STATUS = "meal_status"
CATEGORY_MEAL_REMINDER = "meal_reminder"
# Categories a person can switch off individually. Insights use the older
# ``proactive_enabled`` switch; account notices (trial ending, billing) and
# meal status follow-ups can only be silenced at the OS level.
OPTIONAL_CATEGORIES = frozenset({CATEGORY_MEAL_REMINDER, "daily_summary", "weekly_recap", "reengagement"})
KNOWN_CATEGORIES = OPTIONAL_CATEGORIES | {CATEGORY_INSIGHT, CATEGORY_ACCOUNT, CATEGORY_MEAL_STATUS}

# Suppressions that a later settings change or device registration can undo.
RECOVERABLE_REASONS = (
    "no_device_token",
    "user_disabled",
    "category_disabled",
    "daily_disabled",
    "weekly_disabled",
)


def parse_clock(value: str) -> dt.time:
    try:
        return dt.time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("quiet hours must use HH:MM") from exc


def validate_timezone(value: str) -> str:
    if parse_timezone(value) is None:
        raise ValueError("notification_timezone must be a valid IANA timezone")
    return value


def quiet_hours_end(
    now: dt.datetime,
    *,
    timezone: str,
    start: str,
    end: str,
) -> dt.datetime | None:
    """Return when quiet hours end if ``now`` falls inside them, else None."""
    zone = ZoneInfo(validate_timezone(timezone))
    local = now.astimezone(zone)
    start_time = parse_clock(start)
    end_time = parse_clock(end)
    if start_time == end_time:
        return None
    local_time = local.time().replace(tzinfo=None)
    if start_time > end_time:
        if local_time >= start_time:
            date = local.date() + dt.timedelta(days=1)
        elif local_time < end_time:
            date = local.date()
        else:
            return None
    else:
        if not start_time <= local_time < end_time:
            return None
        date = local.date()
    return dt.datetime.combine(date, end_time, tzinfo=zone).astimezone(dt.UTC)


def user_zone(user: User, preference: InsightNotificationPreference | None) -> ZoneInfo:
    """The zone the app last reported, then the settings zone, then UTC."""
    return (
        parse_timezone(user.timezone)
        or parse_timezone(preference.timezone if preference else None)
        or UTC_ZONE
    )


def _aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


class TypePolicy:
    """Rules owned by one notification type. Defaults allow everything."""

    notification_type: str = ""

    async def preference_reason(
        self,
        delivery: NotificationDelivery,
        preference: InsightNotificationPreference,
    ) -> str | None:
        """Settings-based suppression, checked when scheduling and sending."""
        return None

    async def state_reason(
        self,
        delivery: NotificationDelivery,
        user: User,
        *,
        now: dt.datetime,
    ) -> str | None:
        """Content-based suppression (stale, superseded, cooldown), checked when sending."""
        return None

    async def can_revive(self, delivery: NotificationDelivery) -> bool:
        return True

    async def status_changed(
        self,
        delivery: NotificationDelivery,
        *,
        now: dt.datetime,
    ) -> None:
        """Mirror the delivery state onto the type's own record."""


class PushDispatcher:
    def __init__(
        self,
        db: AsyncSession,
        *,
        gateway: FirebasePushGateway | None = None,
        policies: Mapping[str, TypePolicy] | None = None,
    ):
        self.db = db
        self.gateway = gateway or FirebasePushGateway()
        # Imported here: the proactive_insights package imports this module.
        from app.proactive_insights.analytics import InsightAnalytics

        self.devices = PushDeviceService(db)
        self.analytics = InsightAnalytics(db)
        self.policies = dict(policies or {})
        self._default_policy = TypePolicy()

    def _policy(self, delivery: NotificationDelivery) -> TypePolicy:
        return self.policies.get(delivery.notification_type, self._default_policy)

    async def preferences(self, user_id: int) -> InsightNotificationPreference:
        row = await self.db.get(InsightNotificationPreference, user_id)
        if row is None:
            row = InsightNotificationPreference(user_id=user_id)
            self.db.add(row)
            await self.db.flush()
        return row

    @staticmethod
    def category_enabled(preference: InsightNotificationPreference, category: str) -> bool:
        if category == CATEGORY_INSIGHT:
            return preference.proactive_enabled
        if category in (CATEGORY_ACCOUNT, CATEGORY_MEAL_STATUS):
            return True
        return category not in (preference.disabled_categories or [])

    # -- analytics ---------------------------------------------------------

    async def _record(
        self,
        delivery: NotificationDelivery,
        event_name: str,
        *,
        now: dt.datetime,
        metadata: dict[str, Any] | None = None,
        suffix: str = "",
    ) -> None:
        insight = (
            await self.db.get(ProactiveInsight, delivery.insight_id) if delivery.insight_id else None
        )
        key = f"{insight.id}" if insight else f"delivery:{delivery.id}"
        await self.analytics.record(
            user_id=delivery.user_id,
            event_name=event_name,
            insight=insight,
            category=None if insight else delivery.category,
            metadata={"notification_type": delivery.notification_type, **(metadata or {})},
            event_id=f"{key}:{event_name}{suffix}",
            now=now,
        )

    # -- scheduling --------------------------------------------------------

    async def enqueue(
        self,
        user: User,
        *,
        notification_type: str,
        category: str,
        title: str,
        body: str,
        idempotency_key: str,
        data: dict[str, str] | None = None,
        priority: str = PRIORITY_NORMAL,
        insight_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        not_before: dt.datetime | None = None,
        now: dt.datetime,
    ) -> NotificationDelivery:
        """Create (or reuse) the ledger row for one notification and schedule it.

        ``not_before`` holds a push back (e.g. a grace period in which the
        person may still act in the app); quiet hours can only delay it further.
        """
        if category not in KNOWN_CATEGORIES:
            raise ValueError(f"Unknown notification category: {category}")
        if priority not in PRIORITIES:
            raise ValueError(f"Unknown notification priority: {priority}")
        delivery = await self.db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.idempotency_key == idempotency_key
            )
        )
        if delivery is None:
            delivery = NotificationDelivery(
                user_id=user.id,
                insight_id=insight_id,
                notification_type=notification_type,
                category=category,
                priority=priority,
                title=title,
                body=body,
                data_json=dict(data or {}),
                idempotency_key=idempotency_key,
                status="scheduled",
                scheduled_for=now,
                metadata_json=dict(metadata or {}),
            )
            self.db.add(delivery)
            await self.db.flush()
        if delivery.status in {"sent", "sending"}:
            return delivery
        delivery = await self._schedule(delivery, user, now=now)
        if (
            not_before is not None
            and delivery.status == "scheduled"
            and _aware(delivery.scheduled_for) < not_before
        ):
            delivery.scheduled_for = not_before
        return delivery

    async def _schedule(
        self,
        delivery: NotificationDelivery,
        user: User,
        *,
        now: dt.datetime,
    ) -> NotificationDelivery:
        preference = await self.preferences(user.id)
        reason = await self._settings_reason(delivery, preference)
        if reason is None and not await self.devices.has_devices(user.id):
            reason = "no_device_token"
        if reason is not None:
            return await self._suppress(delivery, reason, now=now)

        delayed_until = None
        if delivery.priority != PRIORITY_TRANSACTIONAL:
            delayed_until = quiet_hours_end(
                now,
                timezone=user_zone(user, preference).key,
                start=preference.quiet_hours_start,
                end=preference.quiet_hours_end,
            )
        delivery.status = "scheduled"
        delivery.scheduled_for = delayed_until or now
        delivery.suppression_reason = None
        await self._policy(delivery).status_changed(delivery, now=now)
        await self._record(
            delivery,
            "notification_scheduled",
            now=now,
            metadata={"delayed_by_quiet_hours": delayed_until is not None},
        )
        return delivery

    async def reschedule_suppressed(self, user: User, *, now: dt.datetime) -> int:
        """Revive recent deliveries a settings change or new device can now reach."""
        rows = list(
            (
                await self.db.scalars(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.user_id == user.id,
                        NotificationDelivery.status == "suppressed",
                        NotificationDelivery.suppression_reason.in_(RECOVERABLE_REASONS),
                        NotificationDelivery.created_at
                        >= now - dt.timedelta(hours=settings.PROACTIVE_NOTIFICATION_MAX_AGE_HOURS),
                    )
                    .order_by(desc(NotificationDelivery.created_at))
                )
            ).all()
        )
        scheduled = 0
        for delivery in rows:
            if not await self._policy(delivery).can_revive(delivery):
                continue
            await self._schedule(delivery, user, now=now)
            scheduled += 1
        return scheduled

    async def _settings_reason(
        self,
        delivery: NotificationDelivery,
        preference: InsightNotificationPreference,
    ) -> str | None:
        if not settings.PUSH_NOTIFICATIONS_ENABLED:
            return "push_disabled"
        if not self.category_enabled(preference, delivery.category):
            return "user_disabled" if delivery.category == CATEGORY_INSIGHT else "category_disabled"
        return await self._policy(delivery).preference_reason(delivery, preference)

    async def _suppress(
        self,
        delivery: NotificationDelivery,
        reason: str,
        *,
        now: dt.datetime,
    ) -> NotificationDelivery:
        delivery.status = "suppressed"
        delivery.suppression_reason = reason
        await self._policy(delivery).status_changed(delivery, now=now)
        await self._record(
            delivery,
            "notification_suppressed",
            now=now,
            metadata={"reason": reason},
            suffix=f":{reason}",
        )
        return delivery

    # -- delivery ----------------------------------------------------------

    async def _cap_reason(
        self,
        delivery: NotificationDelivery,
        user: User,
        preference: InsightNotificationPreference,
        *,
        now: dt.datetime,
    ) -> str | None:
        if delivery.priority != PRIORITY_NORMAL:
            return None
        zone = user_zone(user, preference)
        local = now.astimezone(zone)
        day_start = dt.datetime.combine(local.date(), dt.time.min, tzinfo=zone).astimezone(dt.UTC)

        async def sent_since(start: dt.datetime) -> int:
            return int(
                await self.db.scalar(
                    select(func.count(NotificationDelivery.id)).where(
                        NotificationDelivery.user_id == user.id,
                        NotificationDelivery.id != delivery.id,
                        NotificationDelivery.priority == PRIORITY_NORMAL,
                        NotificationDelivery.sent_at >= start,
                    )
                )
                or 0
            )

        if await sent_since(day_start) >= settings.PROACTIVE_NOTIFICATION_DAILY_LIMIT:
            return "daily_limit"
        if await sent_since(now - dt.timedelta(days=7)) >= settings.PUSH_WEEKLY_LIMIT:
            return "weekly_limit"
        return None

    async def deliver(self, delivery_id: int, *, now: dt.datetime | None = None) -> dict[str, str]:
        now = now or dt.datetime.now(dt.UTC)
        delivery = await self.db.scalar(
            select(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .with_for_update()
        )
        if delivery is None:
            return {"status": "missing"}
        if delivery.status in {"sent", "sending", "suppressed"}:
            return {"status": delivery.status}
        if _aware(delivery.scheduled_for) > now:
            return {"status": "scheduled"}
        user = await self.db.get(User, delivery.user_id)
        if user is None or (delivery.insight_id and await self.db.get(ProactiveInsight, delivery.insight_id) is None):
            delivery.status = "suppressed"
            delivery.suppression_reason = "source_missing"
            await self.db.commit()
            return {"status": "suppressed"}

        policy = self._policy(delivery)
        preference = await self.preferences(user.id)
        reason = await policy.state_reason(delivery, user, now=now)
        reason = reason or await self._settings_reason(delivery, preference)
        tokens = await self.devices.tokens(user.id)
        if reason is None and not tokens:
            reason = "no_device_token"
        if reason is None and delivery.priority != PRIORITY_TRANSACTIONAL:
            quiet_end = quiet_hours_end(
                now,
                timezone=user_zone(user, preference).key,
                start=preference.quiet_hours_start,
                end=preference.quiet_hours_end,
            )
            if quiet_end is not None:
                delivery.status = "scheduled"
                delivery.scheduled_for = quiet_end
                await policy.status_changed(delivery, now=now)
                await self.db.commit()
                return {"status": "scheduled"}
        reason = reason or await self._cap_reason(delivery, user, preference, now=now)
        if reason is not None:
            await self._suppress(delivery, reason, now=now)
            await self.db.commit()
            return {"status": "suppressed"}

        # Commit the at-most-once claim before external send. A crash after FCM
        # accepts remains `sending` and is never retried automatically.
        delivery.status = "sending"
        delivery.attempts += 1
        delivery.last_attempt_at = now
        await policy.status_changed(delivery, now=now)
        title = delivery.title or "Calry"
        body = delivery.body or ""
        data = {
            **{key: str(value) for key, value in (delivery.data_json or {}).items()},
            "delivery_id": str(delivery.id),
        }
        await self.db.commit()

        message_ids: list[str] = []
        errors: list[PushDeliveryError] = []
        for token in tokens:
            try:
                message_ids.append(
                    await self.gateway.send(token=token, title=title, body=body, data=data)
                )
            except PushDeliveryError as exc:
                errors.append(exc)
                if exc.invalidate_token:
                    await self.devices.invalidate(token)

        delivery = await self.db.get(NotificationDelivery, delivery_id)
        if delivery is None:
            return {"status": "missing"}
        if message_ids:
            delivery.status = "sent"
            delivery.provider_message_id = message_ids[0]
            delivery.sent_at = now
            delivery.next_attempt_at = None
            delivery.error_code = errors[0].code if errors else None
            if len(tokens) > 1:
                delivery.metadata_json = {
                    **(delivery.metadata_json or {}),
                    "devices": len(tokens),
                    "devices_reached": len(message_ids),
                }
            await policy.status_changed(delivery, now=now)
            await self._record(delivery, "notification_sent", now=now)
            await self.db.commit()
            return {"status": "sent"}

        error = next((item for item in errors if item.transient), errors[0])
        delivery.error_code = error.code
        if error.transient and delivery.attempts < settings.PROACTIVE_NOTIFICATION_RETRY_MAX:
            delay = settings.PROACTIVE_NOTIFICATION_RETRY_BASE_SECONDS * (2 ** (delivery.attempts - 1))
            delivery.status = "failed"
            delivery.next_attempt_at = now + dt.timedelta(seconds=delay)
            delivery.scheduled_for = delivery.next_attempt_at
        else:
            delivery.status = "suppressed"
            delivery.suppression_reason = "retry_exhausted" if error.transient else "permanent_failure"
        await policy.status_changed(delivery, now=now)
        await self._record(
            delivery,
            "notification_failed",
            now=now,
            metadata={"transient": error.transient, "error_code": error.code},
            suffix=f":{delivery.attempts}",
        )
        if delivery.status == "suppressed":
            await self._record(
                delivery,
                "notification_suppressed",
                now=now,
                metadata={"reason": delivery.suppression_reason},
                suffix=f":{delivery.suppression_reason}",
            )
        await self.db.commit()
        return {"status": delivery.status}


def build_dispatcher(
    db: AsyncSession,
    *,
    gateway: FirebasePushGateway | None = None,
) -> PushDispatcher:
    """Dispatcher with every registered notification type's policy."""
    from app.notifications.meal_nudges import (
        AnalysisReadyPolicy,
        FirstMealPolicy,
        MealReminderPolicy,
    )
    from app.proactive_insights.notifications import ProactiveInsightPolicy

    policies: list[TypePolicy] = [
        ProactiveInsightPolicy(db),
        FirstMealPolicy(db),
        MealReminderPolicy(db),
        AnalysisReadyPolicy(db),
    ]
    return PushDispatcher(
        db,
        gateway=gateway,
        policies={policy.notification_type: policy for policy in policies},
    )
