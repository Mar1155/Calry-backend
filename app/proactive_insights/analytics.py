import datetime as dt
import json
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insight import InsightAnalyticsEvent, ProactiveInsight

TRACKED_EVENTS = frozenset(
    {
        "insight_created",
        "insight_viewed",
        "insight_marked_read",
        "notification_eligible",
        "notification_scheduled",
        "notification_sent",
        "notification_failed",
        "notification_opened",
        "notification_suppressed",
        "insight_dismissed",
        "diary_opened",
        "notification_preferences_changed",
    }
)


def _event_id(parts: object) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(raw.encode()).hexdigest()


class InsightAnalytics:
    """Privacy-minimal product analytics. Never stores meal or insight copy."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        *,
        user_id: int,
        event_name: str,
        insight: ProactiveInsight | None = None,
        source: str = "backend",
        metadata: dict[str, Any] | None = None,
        event_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        if event_name not in TRACKED_EVENTS:
            raise ValueError(f"Unsupported insight analytics event: {event_name}")
        now = now or dt.datetime.now(dt.UTC)
        identifier = event_id or _event_id(
            [user_id, insight.id if insight else None, event_name, source, now.isoformat()]
        )
        if len(identifier) > 64:
            identifier = _event_id(identifier)
        if await self.db.scalar(
            select(InsightAnalyticsEvent.id).where(InsightAnalyticsEvent.event_id == identifier)
        ):
            return
        row = InsightAnalyticsEvent(
            event_id=identifier,
            user_id=user_id,
            insight_id=insight.id if insight else None,
            event_name=event_name,
            category=insight.category if insight else None,
            source=source,
            metadata_json=metadata or {},
            created_at=now,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError:
            return
