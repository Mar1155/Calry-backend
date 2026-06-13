import datetime as dt

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.daily_summary import DailySummary
from app.repositories.base import BaseRepository


class DailySummaryRepository(BaseRepository[DailySummary]):
    """Repository handling all DailySummary queries."""

    def __init__(self, db: AsyncSession):
        super().__init__(DailySummary, db)

    async def get_by_date(self, user_id: int, date_val: dt.date) -> DailySummary | None:
        """Looks up a user's summary for a specific calendar date."""
        stmt = select(DailySummary).where(DailySummary.user_id == user_id, DailySummary.date == date_val)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_history(
        self, user_id: int, skip: int = 0, limit: int = 30, cutoff_date: dt.date | None = None
    ) -> list[DailySummary]:
        """Retrieves a historical list of daily summaries, most recent first."""
        stmt = select(DailySummary).where(DailySummary.user_id == user_id)
        if cutoff_date is not None:
            stmt = stmt.where(DailySummary.date >= cutoff_date)
        stmt = stmt.order_by(desc(DailySummary.date)).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
