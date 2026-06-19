import datetime as dt

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.daily_summary import DailySummary

DAYS_IN_WEEK = 7


class AwarenessService:
    """Computes calm 'soft awareness' metrics from logged calorie activity.

    A day counts as *aware* when the user logged at least one meaningful calorie
    event (reflected as ``consumed_calories > 0`` on that day's DailySummary).

    Awareness rewards showing up and checking in — never eating less, hitting a
    target, or staying in a deficit. There is no "broken" state: a missed day
    simply isn't counted, and the user can return at any time.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _aware_dates(self, user_id: int) -> list[dt.date]:
        """Returns the distinct dates the user was aware, most recent first."""
        stmt = (
            select(DailySummary.date)
            .where(
                DailySummary.user_id == user_id,
                DailySummary.consumed_calories > 0,
            )
            .order_by(desc(DailySummary.date))
        )
        result = await self.db.execute(stmt)
        return sorted({d for d in result.scalars().all()}, reverse=True)

    @staticmethod
    def _week_start(today: dt.date) -> dt.date:
        """Monday of the week containing ``today``."""
        return today - dt.timedelta(days=today.weekday())

    @staticmethod
    def _label(aware_days: int, days_in_week: int = DAYS_IN_WEEK) -> str:
        return f"{aware_days}/{days_in_week} days aware"

    @staticmethod
    def _soft_streak(aware_dates: list[dt.date], today: dt.date) -> int:
        """Consecutive aware days ending today or yesterday.

        Secondary/quiet metric. Returns 0 when the run has lapsed — we never
        surface a "broken streak", just a fresh return.
        """
        if not aware_dates:
            return 0
        yesterday = today - dt.timedelta(days=1)
        if aware_dates[0] not in (today, yesterday):
            return 0
        streak = 0
        expected = aware_dates[0]
        for d in aware_dates:
            if d == expected:
                streak += 1
                expected -= dt.timedelta(days=1)
            else:
                break
        return streak

    async def today(self, user_id: int, today: dt.date | None = None) -> dict:
        today = today or dt.date.today()
        aware = await self._aware_dates(user_id)
        week_start = self._week_start(today)
        aware_this_week = sum(1 for d in aware if week_start <= d <= today)
        has_today = bool(aware) and aware[0] == today
        return {
            "has_logged_today": has_today,
            "aware_today": has_today,
            "aware_days_this_week": aware_this_week,
            "days_in_week": DAYS_IN_WEEK,
            "current_soft_streak": self._soft_streak(aware, today),
            "last_aware_date": aware[0] if aware else None,
            "label": self._label(aware_this_week),
        }

    async def week(self, user_id: int, today: dt.date | None = None) -> dict:
        today = today or dt.date.today()
        aware = set(await self._aware_dates(user_id))
        week_start = self._week_start(today)
        days = []
        aware_days = 0
        for i in range(DAYS_IN_WEEK):
            d = week_start + dt.timedelta(days=i)
            is_aware = d in aware
            if is_aware:
                aware_days += 1
            days.append({"date": d, "is_aware": is_aware})
        return {
            "week_start": week_start,
            "week_end": week_start + dt.timedelta(days=DAYS_IN_WEEK - 1),
            "aware_days": aware_days,
            "days": days,
            "label": self._label(aware_days),
        }
