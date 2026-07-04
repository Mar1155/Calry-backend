import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.revenuecat_event import RevenueCatEvent, RevenueCatSubscriberSnapshot
from app.repositories.base import BaseRepository


class RevenueCatEventRepository(BaseRepository[RevenueCatEvent]):
    """Repository for the webhook idempotency ledger and subscriber snapshots."""

    def __init__(self, db: AsyncSession):
        super().__init__(RevenueCatEvent, db)

    async def get_by_event_id(self, event_id: str) -> RevenueCatEvent | None:
        stmt = select(RevenueCatEvent).where(RevenueCatEvent.event_id == event_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_status(
        self,
        record: RevenueCatEvent,
        status: str,
        error: str | None = None,
    ) -> RevenueCatEvent:
        record.processing_status = status
        record.processing_error = error
        record.processed_at = dt.datetime.now(dt.UTC)
        self.db.add(record)
        await self.db.flush()
        return record

    async def add_snapshot(
        self,
        app_user_id: str,
        user_id: int | None,
        entitlement_active: bool,
        expires_at: dt.datetime | None,
        snapshot: dict,
    ) -> RevenueCatSubscriberSnapshot:
        record = RevenueCatSubscriberSnapshot(
            app_user_id=app_user_id,
            user_id=user_id,
            entitlement_active=entitlement_active,
            expires_at=expires_at,
            snapshot=snapshot,
        )
        self.db.add(record)
        await self.db.flush()
        return record
