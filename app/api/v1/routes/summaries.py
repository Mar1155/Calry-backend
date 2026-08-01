import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.premium import (
    ensure_history_date_access,
    free_history_cutoff,
    has_premium_access,
)
from app.insights.versioning import DomainEvent, InsightVersionService
from app.models.daily_summary import DailySummary
from app.models.user import User
from app.repositories.daily_summary import DailySummaryRepository
from app.schemas.daily_summary import DailySummaryResponse, WaterUpdateRequest
from app.services.summary import SummaryService

router = APIRouter()


@router.post("/water", response_model=DailySummaryResponse)
async def log_water(
    payload: WaterUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailySummary:
    """Adjusts today's water-glass count by a small delta (one tap = one glass).

    The count never goes below zero. Water is a lightweight awareness counter,
    not a tracked goal, so there is no target or validation beyond sane deltas.
    """
    today = dt.date.today()
    summary_service = SummaryService(db)

    summary = await summary_service.sync_daily_summary(current_user.id, today)
    previous_water = summary.water_glasses
    summary.water_glasses = max(0, summary.water_glasses + payload.delta)
    await db.flush()
    if summary.water_glasses != previous_water:
        await InsightVersionService(db).record(
            current_user.id,
            DomainEvent.WATER_LOGGED if summary.water_glasses > previous_water else DomainEvent.WATER_REMOVED,
            affected_date=today,
        )
    return summary


@router.get("/today", response_model=DailySummaryResponse)
async def get_today_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailySummary:
    """Retrieves or creates today's DailySummary.

    Runs a dynamic sync operation to ensure that calorie tallies align exactly with all
    logged meals and active energy expenditures registered today.
    """
    today = dt.date.today()
    summary_service = SummaryService(db)

    # Perform runtime consolidation of today's calorie logs
    summary = await summary_service.sync_daily_summary(current_user.id, today)
    return summary


@router.get("/date/{date_val}", response_model=DailySummaryResponse)
async def get_summary_for_date(
    date_val: dt.date,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailySummary:
    """Returns an up-to-date summary for one accessible calendar day."""
    if date_val > dt.date.today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Future daily summaries are unavailable.",
        )
    await ensure_history_date_access(date_val, current_user, db)
    return await SummaryService(db).sync_daily_summary(current_user.id, date_val)


@router.get("/history", response_model=list[DailySummaryResponse])
async def get_historical_summaries(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=90),  # Limit default to 30 days (1 month)
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DailySummary]:
    """Retrieves a historical list of daily summaries, sorted chronologically descending."""
    repo = DailySummaryRepository(db)

    cutoff_date = None
    if not await has_premium_access(current_user, db):
        cutoff_date = free_history_cutoff()

    return await repo.get_history(
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        cutoff_date=cutoff_date,
    )
