"""Proactive insight pushes on top of the shared push dispatcher."""

import datetime as dt
import logging
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.insight import (
    InsightNotificationPreference,
    NotificationDelivery,
    ProactiveInsight,
)
from app.models.user import User
from app.notifications.dispatcher import (
    CATEGORY_INSIGHT,
    PRIORITY_DIGEST,
    PRIORITY_NORMAL,
    PushDispatcher,
    TypePolicy,
    parse_clock,
    quiet_hours_end,
    validate_timezone,
)
from app.notifications.gateway import FirebasePushGateway, PushDeliveryError
from app.proactive_insights.analytics import InsightAnalytics

__all__ = [
    "FirebasePushGateway",
    "InsightNotificationService",
    "ProactiveInsightPolicy",
    "PushDeliveryError",
    "parse_clock",
    "quiet_hours_end",
    "validate_timezone",
]

logger = logging.getLogger("app.proactive_insights.notifications")

NOTIFICATION_TYPE = "proactive_insight"


def _is_weekly(insight: ProactiveInsight) -> bool:
    return insight.evidence_json.get("evidence", {}).get("source_trigger") == "WeeklyEvaluation"


def _is_daily(insight: ProactiveInsight) -> bool:
    return insight.evidence_json.get("evidence", {}).get("source_trigger") == "DailyEvaluation"


def _aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def _push_copy(insight: ProactiveInsight) -> tuple[str, str, dict[str, str]]:
    return (
        "Something Calry noticed",
        insight.title.rstrip(".") + ".",
        {
            "type": NOTIFICATION_TYPE,
            "insight_id": insight.id,
            "route": f"/insights/{insight.id}",
        },
    )


class ProactiveInsightPolicy(TypePolicy):
    notification_type = NOTIFICATION_TYPE

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _insight(self, delivery: NotificationDelivery) -> ProactiveInsight | None:
        if delivery.insight_id is None:
            return None
        return await self.db.get(ProactiveInsight, delivery.insight_id)

    async def preference_reason(
        self,
        delivery: NotificationDelivery,
        preference: InsightNotificationPreference,
    ) -> str | None:
        if not settings.PROACTIVE_PUSH_ENABLED:
            return "push_disabled"
        insight = await self._insight(delivery)
        if insight is None:
            return "source_missing"
        if _is_weekly(insight) and not preference.weekly_enabled:
            return "weekly_disabled"
        if _is_daily(insight) and not preference.daily_enabled:
            return "daily_disabled"
        return None

    async def state_reason(
        self,
        delivery: NotificationDelivery,
        user: User,
        *,
        now: dt.datetime,
    ) -> str | None:
        insight = await self._insight(delivery)
        if insight is None:
            return "source_missing"
        if insight.notification_sent_at is not None:
            return "already_sent"
        if insight.superseded_at is not None:
            return "superseded"
        max_age = dt.timedelta(hours=settings.PROACTIVE_NOTIFICATION_MAX_AGE_HOURS)
        if _aware(insight.created_at) < now - max_age:
            return "stale"
        cooldown = settings.proactive_insight_type_cooldowns.get(
            insight.type, settings.PROACTIVE_INSIGHT_COOLDOWN_DAYS
        )
        recent_similar = await self.db.scalar(
            select(ProactiveInsight.id)
            .where(
                ProactiveInsight.user_id == user.id,
                ProactiveInsight.id != insight.id,
                ProactiveInsight.dedup_key == insight.dedup_key,
                ProactiveInsight.notification_sent_at >= now - dt.timedelta(days=cooldown),
            )
            .limit(1)
        )
        return "cooldown" if recent_similar else None

    async def can_revive(self, delivery: NotificationDelivery) -> bool:
        insight = await self._insight(delivery)
        if insight is None or insight.superseded_at is not None:
            return False
        if delivery.title is None:
            # Ledger rows created before copy was stored on the delivery.
            delivery.title, delivery.body, delivery.data_json = _push_copy(insight)
        return True

    async def status_changed(
        self,
        delivery: NotificationDelivery,
        *,
        now: dt.datetime,
    ) -> None:
        insight = await self._insight(delivery)
        if insight is None:
            return
        if delivery.status == "suppressed" and delivery.suppression_reason in {
            "permanent_failure",
            "retry_exhausted",
        }:
            insight.notification_status = "failed"
        else:
            insight.notification_status = delivery.status
        if delivery.status == "sent":
            insight.notification_sent_at = delivery.sent_at or now


class InsightNotificationService:
    """Schedules proactive insights as pushes; delivery is type-neutral."""

    def __init__(self, db: AsyncSession, *, gateway: FirebasePushGateway | None = None):
        self.db = db
        self.policy = ProactiveInsightPolicy(db)
        self.dispatcher = PushDispatcher(
            db,
            gateway=gateway,
            policies={NOTIFICATION_TYPE: self.policy},
        )
        self.analytics = InsightAnalytics(db)

    async def preferences(self, user_id: int) -> InsightNotificationPreference:
        return await self.dispatcher.preferences(user_id)

    async def schedule(
        self,
        insight: ProactiveInsight,
        user: User,
        *,
        now: dt.datetime,
    ) -> NotificationDelivery | None:
        if insight.notification_status != "ready":
            return None
        await self.analytics.record(
            user_id=user.id,
            event_name="notification_eligible",
            insight=insight,
            event_id=f"{insight.id}:notification_eligible",
            now=now,
        )
        title, body, data = _push_copy(insight)
        return await self.dispatcher.enqueue(
            user,
            notification_type=NOTIFICATION_TYPE,
            category=CATEGORY_INSIGHT,
            # Weekly observations were never limited by the one-a-day cap.
            priority=PRIORITY_DIGEST if _is_weekly(insight) else PRIORITY_NORMAL,
            title=title,
            body=body,
            data=data,
            idempotency_key=sha256(f"proactive-push:{insight.id}".encode()).hexdigest(),
            insight_id=insight.id,
            now=now,
        )

    async def reschedule_suppressed(self, user: User, *, now: dt.datetime) -> int:
        return await self.dispatcher.reschedule_suppressed(user, now=now)

    async def reschedule_after_token(self, user: User, *, now: dt.datetime) -> int:
        """Backward-compatible name used by the device-token endpoint."""
        return await self.reschedule_suppressed(user, now=now)

    async def deliver(self, delivery_id: int, *, now: dt.datetime | None = None) -> dict[str, str]:
        return await self.dispatcher.deliver(delivery_id, now=now)
