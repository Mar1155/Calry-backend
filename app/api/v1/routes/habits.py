import datetime as dt
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.daily_summary import DailySummary
from app.models.user import User

logger = logging.getLogger("app.api.habits")
router = APIRouter()


class StreakResponse(BaseModel):
    current_streak: int
    longest_streak: int
    days_logged_this_week: int
    last_logged_date: dt.date | None


@router.get("/streak", response_model=StreakResponse)
async def get_streak(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreakResponse:
    """Computes the user's current and longest logging streak from DailySummary data."""
    stmt = (
        select(DailySummary)
        .where(DailySummary.user_id == current_user.id, DailySummary.consumed_calories > 0)
        .order_by(desc(DailySummary.date))
    )
    result = await db.execute(stmt)
    summaries = result.scalars().all()

    if not summaries:
        return StreakResponse(current_streak=0, longest_streak=0, days_logged_this_week=0, last_logged_date=None)

    logged_dates = sorted({s.date for s in summaries}, reverse=True)

    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)

    # Current streak: consecutive days ending today or yesterday
    current_streak = 0
    if logged_dates and logged_dates[0] in (today, yesterday):
        expected = logged_dates[0]
        for d in logged_dates:
            if d == expected:
                current_streak += 1
                expected = expected - dt.timedelta(days=1)
            else:
                break

    # Longest streak: scan all logged dates
    longest_streak = 0
    run = 0
    prev_date = None
    for d in sorted(logged_dates):
        if prev_date is None or d == prev_date + dt.timedelta(days=1):
            run += 1
        else:
            run = 1
        longest_streak = max(longest_streak, run)
        prev_date = d

    # Days logged this week (Mon-Sun)
    week_start = today - dt.timedelta(days=today.weekday())
    days_logged_this_week = sum(1 for d in logged_dates if d >= week_start)

    last_logged = logged_dates[0] if logged_dates else None

    return StreakResponse(
        current_streak=current_streak,
        longest_streak=longest_streak,
        days_logged_this_week=days_logged_this_week,
        last_logged_date=last_logged,
    )
