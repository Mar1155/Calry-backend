import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.burned_calories import BurnedCalories
from app.models.user import User
from app.repositories.burned_calories import BurnedCaloriesRepository
from app.schemas.burned_calories import (
    BurnedCaloriesCreate,
    BurnedCaloriesResponse,
)
from app.services.summary import SummaryService

logger = logging.getLogger("app.api.burned_calories")
router = APIRouter()


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
    """Logs active calories burned (manual exercise or synced mobile health kit).

    Immediately synchronizes the user's DailySummary for the logged day.
    """
    repo = BurnedCaloriesRepository(db)

    # 1. Save burned calorie entry
    entry = BurnedCalories(
        user_id=current_user.id,
        activity_name=payload.activity_name,
        calories=payload.calories,
    )
    await repo.create(entry)
    await db.flush()

    # 2. Re-calculate DailySummary balance for this exact calendar day
    try:
        summary_service = SummaryService(db)
        await summary_service.sync_daily_summary(current_user.id, entry.created_at.date())
    except Exception as e:
        logger.error(f"Failed to synchronize daily summary following calorie burn log: {e}")

    return entry


@router.get("", response_model=list[BurnedCaloriesResponse])
async def list_burned_calories(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[BurnedCalories]:
    """Retrieves a historical list of active calorie-burn entries registered by the user."""
    repo = BurnedCaloriesRepository(db)
    return await repo.get_by_user(user_id=current_user.id, skip=skip, limit=limit)
