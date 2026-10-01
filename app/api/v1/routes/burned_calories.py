import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.premium import ensure_history_date_access
from app.insights.versioning import DomainEvent, InsightVersionService
from app.models.burned_calories import BurnedCalories
from app.models.user import User
from app.repositories.burned_calories import BurnedCaloriesRepository
from app.schemas.burned_calories import (
    BurnedCaloriesCreate,
    BurnedCaloriesResponse,
    BurnedCaloriesUpdate,
)
from app.services.summary import SummaryService

logger = logging.getLogger("app.api.burned_calories")
router = APIRouter()


def logged_moment(day: dt.date) -> dt.datetime:
    """Timestamp for an entry logged onto ``day`` after the fact. Days are UTC
    calendar days (see get_user_burned_on_date), so midday keeps it inside."""
    return dt.datetime.combine(day, dt.time(12, 0), tzinfo=dt.UTC)


async def _sync_day(db: AsyncSession, user_id: int, day: dt.date, event: DomainEvent) -> None:
    try:
        await SummaryService(db).sync_daily_summary(user_id, day)
    except Exception as e:
        logger.error(f"Failed to synchronize daily summary following calorie burn change: {e}")
    await InsightVersionService(db).record(user_id, event, affected_date=day)


async def _owned_entry(db: AsyncSession, entry_id: int, user: User) -> BurnedCalories:
    entry = await BurnedCaloriesRepository(db).get(entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found.")
    await ensure_history_date_access(entry.created_at.date(), user, db)
    return entry


@router.post(
    "",
    response_model=BurnedCaloriesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def log_energy_expenditure(
    payload: BurnedCaloriesCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BurnedCalories:
    """Logs active calories burned (manual exercise or synced mobile health kit),
    today or onto any past ``date``.

    Immediately synchronizes the user's DailySummary for the logged day.
    """
    entry = BurnedCalories(
        user_id=current_user.id,
        activity_name=payload.activity_name,
        calories=payload.calories,
    )
    if payload.date is not None:
        if payload.date > dt.date.today():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Activity cannot be logged on a future date.",
            )
        await ensure_history_date_access(payload.date, current_user, db)
        entry.created_at = logged_moment(payload.date)
    await BurnedCaloriesRepository(db).create(entry)
    await db.flush()

    await _sync_day(db, current_user.id, entry.created_at.date(), DomainEvent.ACTIVITY_LOGGED)
    return entry


@router.get("", response_model=list[BurnedCaloriesResponse])
async def list_burned_calories(
    date: dt.date | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[BurnedCalories]:
    """Lists the user's activity entries, newest first, optionally for one day."""
    repo = BurnedCaloriesRepository(db)
    if date is not None:
        await ensure_history_date_access(date, current_user, db)
        entries = await repo.get_user_burned_on_date(current_user.id, date)
        return sorted(entries, key=lambda e: e.created_at, reverse=True)
    return await repo.get_by_user(user_id=current_user.id, skip=skip, limit=limit)


@router.patch("/{entry_id}", response_model=BurnedCaloriesResponse)
async def update_burned_calories(
    entry_id: int,
    payload: BurnedCaloriesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BurnedCalories:
    """Edits an activity entry on any day; resyncs that day's summary."""
    entry = await _owned_entry(db, entry_id, current_user)
    changes = payload.model_dump(exclude_unset=True)
    if "calories" in changes and changes["calories"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Calories cannot be empty.",
        )
    for field, value in changes.items():
        setattr(entry, field, value)
    await db.flush()
    await _sync_day(db, current_user.id, entry.created_at.date(), DomainEvent.ACTIVITY_LOGGED)
    return entry


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_burned_calories(
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Removes an activity entry on any day; resyncs that day's summary."""
    entry = await _owned_entry(db, entry_id, current_user)
    day = entry.created_at.date()
    await db.delete(entry)
    await db.flush()
    await _sync_day(db, current_user.id, day, DomainEvent.ACTIVITY_DELETED)
