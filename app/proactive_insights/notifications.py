import datetime as dt
import logging
from hashlib import sha256
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from firebase_admin import messaging
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import init_firebase
from app.models.insight import (
    InsightNotificationDelivery,
    InsightNotificationPreference,
    ProactiveInsight,
)
from app.models.user import User
from app.proactive_insights.analytics import InsightAnalytics

logger = logging.getLogger("app.proactive_insights.notifications")


class PushDeliveryError(RuntimeError):
    def __init__(self, code: str, *, transient: bool, invalidate_token: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.invalidate_token = invalidate_token


class FirebasePushGateway:
    async def send(
        self,
        *,
        token: str,
        title: str,
        body: str,
        data: dict[str, str],
    ) -> str:
        try:
            init_firebase()
            return messaging.send(
                messaging.Message(
                    token=token,
                    notification=messaging.Notification(title=title, body=body),
                    data=data,
                    android=messaging.AndroidConfig(priority="normal"),
                    apns=messaging.APNSConfig(
                        headers={"apns-priority": "5"},
                        payload=messaging.APNSPayload(
                            aps=messaging.Aps(sound="default", content_available=False)
                        ),
                    ),
                )
            )
        except Exception as exc:
            name = type(exc).__name__
            transient = name in {
                "AbortedError",
                "CancelledError",
                "UnavailableError",
                "ResourceExhaustedError",
                "QuotaExceededError",
                "InternalError",
                "DeadlineExceededError",
            }
            raise PushDeliveryError(
                name,
                transient=transient,
                invalidate_token=name in {"UnregisteredError", "SenderIdMismatchError"},
            ) from exc


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("notification_timezone must be a valid IANA timezone") from exc
    return value


def parse_clock(value: str) -> dt.time:
    try:
        return dt.time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("quiet hours must use HH:MM") from exc


def quiet_hours_end(
    now: dt.datetime,
    *,
    timezone: str,
    start: str,
    end: str,
) -> dt.datetime | None:
    zone = ZoneInfo(validate_timezone(timezone))
    local = now.astimezone(zone)
    start_time = parse_clock(start)
    end_time = parse_clock(end)
    if start_time == end_time:
        return None
    local_time = local.time().replace(tzinfo=None)
    overnight = start_time > end_time
    if overnight:
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
    allowed_local = dt.datetime.combine(date, end_time, tzinfo=zone)
    return allowed_local.astimezone(dt.UTC)


class InsightNotificationService:
    def __init__(self, db: AsyncSession, *, gateway: FirebasePushGateway | None = None):
        self.db = db
        self.gateway = gateway or FirebasePushGateway()
        self.analytics = InsightAnalytics(db)

    async def preferences(self, user_id: int) -> InsightNotificationPreference:
        row = await self.db.get(InsightNotificationPreference, user_id)
        if row is None:
            row = InsightNotificationPreference(user_id=user_id)
            self.db.add(row)
            await self.db.flush()
        return row

    @staticmethod
    def _is_weekly(insight: ProactiveInsight) -> bool:
        return insight.evidence_json.get("evidence", {}).get("source_trigger") == "WeeklyEvaluation"

    async def _suppress(
        self,
        insight: ProactiveInsight,
        reason: str,
        *,
        now: dt.datetime,
    ) -> InsightNotificationDelivery:
        delivery = await self._delivery(insight, now=now)
        delivery.status = "suppressed"
        delivery.suppression_reason = reason
        insight.notification_status = "suppressed"
        await self.analytics.record(
            user_id=insight.user_id,
            event_name="notification_suppressed",
            insight=insight,
            metadata={"reason": reason},
            event_id=f"{insight.id}:notification_suppressed:{reason}",
            now=now,
        )
        return delivery

    async def _delivery(
        self, insight: ProactiveInsight, *, now: dt.datetime
    ) -> InsightNotificationDelivery:
        existing = await self.db.scalar(
            select(InsightNotificationDelivery).where(
                InsightNotificationDelivery.insight_id == insight.id
            )
        )
        if existing is not None:
            return existing
        delivery = InsightNotificationDelivery(
            insight_id=insight.id,
            user_id=insight.user_id,
            idempotency_key=sha256(f"proactive-push:{insight.id}".encode()).hexdigest(),
            status="scheduled",
            scheduled_for=now,
            metadata_json={},
        )
        self.db.add(delivery)
        await self.db.flush()
        return delivery

    async def schedule(
        self,
        insight: ProactiveInsight,
        user: User,
        *,
        now: dt.datetime,
    ) -> InsightNotificationDelivery | None:
        if insight.notification_status != "ready":
            return None
        await self.analytics.record(
            user_id=user.id,
            event_name="notification_eligible",
            insight=insight,
            event_id=f"{insight.id}:notification_eligible",
            now=now,
        )
        preference = await self.preferences(user.id)
        if not settings.PROACTIVE_PUSH_ENABLED:
            return await self._suppress(insight, "push_disabled", now=now)
        if not preference.proactive_enabled:
            return await self._suppress(insight, "user_disabled", now=now)
        if self._is_weekly(insight) and not preference.weekly_enabled:
            return await self._suppress(insight, "weekly_disabled", now=now)
        if not self._is_weekly(insight) and insight.evidence_json.get("evidence", {}).get(
            "source_trigger"
        ) == "DailyEvaluation" and not preference.daily_enabled:
            return await self._suppress(insight, "daily_disabled", now=now)
        if not user.fcm_token:
            return await self._suppress(insight, "no_device_token", now=now)

        delivery = await self._delivery(insight, now=now)
        delayed_until = quiet_hours_end(
            now,
            timezone=preference.timezone,
            start=preference.quiet_hours_start,
            end=preference.quiet_hours_end,
        )
        delivery.status = "scheduled"
        delivery.scheduled_for = delayed_until or now
        delivery.suppression_reason = None
        insight.notification_status = "scheduled"
        await self.analytics.record(
            user_id=user.id,
            event_name="notification_scheduled",
            insight=insight,
            metadata={"delayed_by_quiet_hours": delayed_until is not None},
            event_id=f"{insight.id}:notification_scheduled",
            now=now,
        )
        return delivery

    async def reschedule_suppressed(self, user: User, *, now: dt.datetime) -> int:
        recoverable_reasons = (
            "no_device_token",
            "user_disabled",
            "daily_disabled",
            "weekly_disabled",
        )
        rows = list(
            (
                await self.db.scalars(
                    select(InsightNotificationDelivery)
                    .where(
                        InsightNotificationDelivery.user_id == user.id,
                        InsightNotificationDelivery.status == "suppressed",
                        InsightNotificationDelivery.suppression_reason.in_(recoverable_reasons),
                        InsightNotificationDelivery.created_at
                        >= now - dt.timedelta(hours=settings.PROACTIVE_NOTIFICATION_MAX_AGE_HOURS),
                    )
                    .order_by(desc(InsightNotificationDelivery.created_at))
                )
            ).all()
        )
        scheduled = 0
        for delivery in rows:
            insight = await self.db.get(ProactiveInsight, delivery.insight_id)
            if insight is None or insight.superseded_at is not None:
                continue
            insight.notification_status = "ready"
            await self.schedule(insight, user, now=now)
            scheduled += 1
        return scheduled

    async def reschedule_after_token(self, user: User, *, now: dt.datetime) -> int:
        """Backward-compatible name used by the device-token endpoint."""
        return await self.reschedule_suppressed(user, now=now)

    async def _local_day_bounds(
        self, now: dt.datetime, preference: InsightNotificationPreference
    ) -> tuple[dt.datetime, dt.datetime]:
        zone = ZoneInfo(validate_timezone(preference.timezone))
        local = now.astimezone(zone)
        start_local = dt.datetime.combine(local.date(), dt.time.min, tzinfo=zone)
        return start_local.astimezone(dt.UTC), (
            start_local + dt.timedelta(days=1)
        ).astimezone(dt.UTC)

    async def _suppression_reason(
        self,
        insight: ProactiveInsight,
        user: User,
        preference: InsightNotificationPreference,
        *,
        now: dt.datetime,
    ) -> tuple[str | None, dt.datetime | None]:
        if insight.notification_sent_at is not None:
            return "already_sent", None
        if insight.superseded_at is not None:
            return "superseded", None
        created = (
            insight.created_at.replace(tzinfo=dt.UTC)
            if insight.created_at.tzinfo is None
            else insight.created_at
        )
        if created < now - dt.timedelta(hours=settings.PROACTIVE_NOTIFICATION_MAX_AGE_HOURS):
            return "stale", None
        if not preference.proactive_enabled:
            return "user_disabled", None
        if self._is_weekly(insight) and not preference.weekly_enabled:
            return "weekly_disabled", None
        if (
            not self._is_weekly(insight)
            and insight.evidence_json.get("evidence", {}).get("source_trigger")
            == "DailyEvaluation"
            and not preference.daily_enabled
        ):
            return "daily_disabled", None
        if not user.fcm_token:
            return "no_device_token", None
        quiet_end = quiet_hours_end(
            now,
            timezone=preference.timezone,
            start=preference.quiet_hours_start,
            end=preference.quiet_hours_end,
        )
        if quiet_end is not None:
            return "quiet_hours", quiet_end

        if not self._is_weekly(insight):
            day_start, day_end = await self._local_day_bounds(now, preference)
            sent_insights = list(
                (
                    await self.db.scalars(
                        select(ProactiveInsight).where(
                            ProactiveInsight.user_id == user.id,
                            ProactiveInsight.notification_sent_at >= day_start,
                            ProactiveInsight.notification_sent_at < day_end,
                        )
                    )
                ).all()
            )
            if (
                sum(not self._is_weekly(item) for item in sent_insights)
                >= settings.PROACTIVE_NOTIFICATION_DAILY_LIMIT
            ):
                return "daily_limit", None

        cooldown = settings.proactive_insight_type_cooldowns.get(
            insight.type, settings.PROACTIVE_INSIGHT_COOLDOWN_DAYS
        )
        recent_similar = await self.db.scalar(
            select(ProactiveInsight.id)
            .where(
                ProactiveInsight.user_id == user.id,
                ProactiveInsight.id != insight.id,
                ProactiveInsight.dedup_key == insight.dedup_key,
                ProactiveInsight.notification_sent_at
                >= now - dt.timedelta(days=cooldown),
            )
            .limit(1)
        )
        if recent_similar:
            return "cooldown", None
        return None, None

    async def deliver(self, delivery_id: int, *, now: dt.datetime | None = None) -> dict[str, str]:
        now = now or dt.datetime.now(dt.UTC)
        delivery = await self.db.scalar(
            select(InsightNotificationDelivery)
            .where(InsightNotificationDelivery.id == delivery_id)
            .with_for_update()
        )
        if delivery is None:
            return {"status": "missing"}
        if delivery.status in {"sent", "sending", "suppressed"}:
            return {"status": delivery.status}
        scheduled_for = delivery.scheduled_for
        if scheduled_for.tzinfo is None:
            scheduled_for = scheduled_for.replace(tzinfo=dt.UTC)
        if scheduled_for > now:
            return {"status": "scheduled"}
        insight = await self.db.get(ProactiveInsight, delivery.insight_id)
        user = await self.db.get(User, delivery.user_id)
        if insight is None or user is None:
            delivery.status = "suppressed"
            delivery.suppression_reason = "source_missing"
            await self.db.commit()
            return {"status": "suppressed"}
        preference = await self.preferences(user.id)
        reason, delayed_until = await self._suppression_reason(
            insight, user, preference, now=now
        )
        if reason == "quiet_hours" and delayed_until is not None:
            delivery.status = "scheduled"
            delivery.scheduled_for = delayed_until
            insight.notification_status = "scheduled"
            await self.db.commit()
            return {"status": "scheduled"}
        if reason is not None:
            await self._suppress(insight, reason, now=now)
            await self.db.commit()
            return {"status": "suppressed"}

        # Commit the at-most-once claim before external send. A crash after FCM
        # accepts remains `sending` and is never retried automatically.
        delivery.status = "sending"
        delivery.attempts += 1
        delivery.last_attempt_at = now
        insight.notification_status = "sending"
        token = user.fcm_token or ""
        await self.db.commit()

        try:
            message_id = await self.gateway.send(
                token=token,
                title="Something Calry noticed",
                body=insight.title.rstrip(".") + ".",
                data={
                    "type": "proactive_insight",
                    "insight_id": insight.id,
                    "route": f"/insights/{insight.id}",
                },
            )
        except PushDeliveryError as exc:
            delivery = await self.db.get(InsightNotificationDelivery, delivery_id)
            insight = await self.db.get(ProactiveInsight, insight.id)
            if delivery is None or insight is None:
                return {"status": "missing"}
            delivery.error_code = exc.code
            if exc.transient and delivery.attempts < settings.PROACTIVE_NOTIFICATION_RETRY_MAX:
                delay = settings.PROACTIVE_NOTIFICATION_RETRY_BASE_SECONDS * (
                    2 ** (delivery.attempts - 1)
                )
                delivery.status = "failed"
                delivery.next_attempt_at = now + dt.timedelta(seconds=delay)
                delivery.scheduled_for = delivery.next_attempt_at
                insight.notification_status = "failed"
            else:
                delivery.status = "suppressed"
                delivery.suppression_reason = "retry_exhausted" if exc.transient else "permanent_failure"
                insight.notification_status = "failed"
                if exc.invalidate_token and user.fcm_token == token:
                    user.fcm_token = None
            await self.analytics.record(
                user_id=user.id,
                event_name="notification_failed",
                insight=insight,
                metadata={"transient": exc.transient, "error_code": exc.code},
                event_id=f"{insight.id}:notification_failed:{delivery.attempts}",
                now=now,
            )
            if delivery.status == "suppressed":
                await self.analytics.record(
                    user_id=user.id,
                    event_name="notification_suppressed",
                    insight=insight,
                    metadata={"reason": delivery.suppression_reason or "delivery_failed"},
                    event_id=(
                        f"{insight.id}:notification_suppressed:"
                        f"{delivery.suppression_reason or 'delivery_failed'}"
                    ),
                    now=now,
                )
            await self.db.commit()
            return {"status": delivery.status}

        delivery = await self.db.get(InsightNotificationDelivery, delivery_id)
        insight = await self.db.get(ProactiveInsight, insight.id)
        if delivery is None or insight is None:
            return {"status": "missing"}
        delivery.status = "sent"
        delivery.provider_message_id = message_id
        delivery.sent_at = now
        delivery.next_attempt_at = None
        delivery.error_code = None
        insight.notification_status = "sent"
        insight.notification_sent_at = now
        await self.analytics.record(
            user_id=insight.user_id,
            event_name="notification_sent",
            insight=insight,
            event_id=f"{insight.id}:notification_sent",
            now=now,
        )
        await self.db.commit()
        return {"status": "sent"}
