import datetime as dt

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.burned_calories import BurnedCalories
from app.repositories.base import BaseRepository


class BurnedCaloriesRepository(BaseRepository[BurnedCalories]):
    """Repository handling all calorie expenditure data queries."""

    def __init__(self, db: AsyncSession):
        super().__init__(BurnedCalories, db)

    async def get_by_user(self, user_id: int, skip: int = 0, limit: int = 50) -> list[BurnedCalories]:
        """Gets a list of burned calorie entries sorted by date descending."""
        stmt = (
            select(BurnedCalories)
            .where(BurnedCalories.user_id == user_id)
            .order_by(desc(BurnedCalories.created_at))
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_user_burned_on_date(self, user_id: int, date_val: dt.date) -> list[BurnedCalories]:
        """Gets all burned calories entries registered on a specific calendar date."""
        start_dt = dt.datetime.combine(date_val, dt.time.min).replace(tzinfo=dt.UTC)
        end_dt = dt.datetime.combine(date_val, dt.time.max).replace(tzinfo=dt.UTC)
        stmt = select(BurnedCalories).where(
            BurnedCalories.user_id == user_id,
            BurnedCalories.created_at >= start_dt,
            BurnedCalories.created_at <= end_dt,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
