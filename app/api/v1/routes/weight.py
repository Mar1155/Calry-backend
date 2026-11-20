import datetime as dt
import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_time import client_today
from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.insights.versioning import DomainEvent, InsightVersionService
from app.models.user import User
from app.models.weight_entry import WeightEntry
from app.schemas.weight import (
    AdaptiveTargetApply,
    AdaptiveTargetResponse,
    WeightEntryCreate,
    WeightEntryResponse,
    WeightEntryUpdate,
    WeightOverviewResponse,
)
from app.services.summary import SummaryService
from app.services.weight_service import (
    WINDOW_DAYS,
    AdaptiveTarget,
    WeightPoint,
    WeightService,
    slope_kg_per_day,
    smoothed_trend,
)

logger = logging.getLogger("app.api.weight")
router = APIRouter()


def _adaptive_response(result: AdaptiveTarget) -> AdaptiveTargetResponse:
    return AdaptiveTargetResponse(**asdict(result), change=result.change)


async def _owned_entry(db: AsyncSession, entry_id: int, user: User) -> WeightEntry:
    entry = await db.get(WeightEntry, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Weight entry not found.")
    return entry


async def _record_change(db: AsyncSession, user_id: int, day: dt.date) -> None:
    await InsightVersionService(db).record(user_id, DomainEvent.WEIGHT_UPDATED, affected_date=day)


@router.get("", response_model=WeightOverviewResponse)
async def weight_overview(
    days: int = Query(default=90, ge=7, le=3650),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WeightOverviewResponse:
    """Weigh-ins in the last ``days`` with their smoothed trend, the recent
    rate of change and the adaptive calorie target assessment."""
    service = WeightService(db)
    today = client_today()
    # The trend is smoothed over the whole history so the first point in
    # range does not restart from a raw (noisy) value.
    all_entries = await service.entries(current_user.id)
    points = [WeightPoint(e.local_date, e.weight_kg) for e in all_entries]
    trend = smoothed_trend(points)
    since = today - dt.timedelta(days=days - 1)
    in_range = [
        WeightEntryResponse.model_validate(entry).model_copy(update={"trend_kg": trend_value})
        for entry, trend_value in zip(all_entries, trend, strict=True)
        if entry.local_date >= since
    ]
    recent = [p for p in points if p.date >= today - dt.timedelta(days=WINDOW_DAYS)]
    slope = slope_kg_per_day(recent) if recent and (recent[-1].date - recent[0].date).days >= 7 else None
    adaptive = await service.adaptive_target(current_user, today)
    return WeightOverviewResponse(
        entries=in_range,
        latest_kg=all_entries[-1].weight_kg if all_entries else None,
        trend_kg=trend[-1] if trend else None,
        weekly_change_kg=round(slope * 7, 2) if slope is not None else None,
        change_in_range_kg=round(in_range[-1].trend_kg - in_range[0].trend_kg, 2) if len(in_range) >= 2 else None,
        goal_type=current_user.goal_type,
        adaptive=_adaptive_response(adaptive),
    )


@router.post("/entries", response_model=WeightEntryResponse, status_code=status.HTTP_201_CREATED)
async def log_weight(
    payload: WeightEntryCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WeightEntry:
    """Logs a weigh-in today or on a past ``date``. A day holds one value:
    logging again on the same day replaces it."""
    today = client_today()
    day = payload.date or today
    if day > today:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Weight cannot be logged on a future date.",
        )
    entry = await WeightService(db).upsert(current_user, day, payload.weight_kg)
    await _record_change(db, current_user.id, day)
    return entry


@router.patch("/entries/{entry_id}", response_model=WeightEntryResponse)
async def update_weight(
    entry_id: int,
    payload: WeightEntryUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WeightEntry:
    entry = await _owned_entry(db, entry_id, current_user)
    entry.weight_kg = round(payload.weight_kg, 2)
    await db.flush()
    await WeightService(db).sync_current_weight(current_user)
    await _record_change(db, current_user.id, entry.local_date)
    return entry


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_weight(
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Removes a weigh-in. The current weight falls back to the previous one;
    the last remaining weigh-in cannot be removed, since the calorie formula
    needs a weight."""
    entry = await _owned_entry(db, entry_id, current_user)
    service = WeightService(db)
    if len(await service.entries(current_user.id)) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "LAST_WEIGHT_ENTRY"},
        )
    day = entry.local_date
    await db.delete(entry)
    await db.flush()
    await service.sync_current_weight(current_user)
    await _record_change(db, current_user.id, day)


@router.post("/adaptive-target/apply", response_model=AdaptiveTargetResponse)
async def apply_adaptive_target(
    payload: AdaptiveTargetApply,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdaptiveTargetResponse:
    """Sets the daily target to the current suggestion, re-evaluated here so a
    stale screen cannot apply a value the data no longer supports."""
    today = client_today()
    service = WeightService(db)
    result = await service.adaptive_target(current_user, today)
    if result.status != "adjust" or result.recommended_goal != payload.recommended_goal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ADAPTIVE_TARGET_CHANGED"},
        )
    previous = current_user.daily_calorie_goal
    current_user.daily_calorie_goal = result.recommended_goal
    current_user.calorie_target_source = "adaptive"
    current_user.adaptive_target_reviewed_at = dt.datetime.now(dt.UTC)
    await db.flush()
    try:
        await SummaryService(db).sync_daily_summary(current_user.id, today)
    except Exception as e:
        logger.error(f"Failed to sync today's summary after adaptive target: {e}")
    await InsightVersionService(db).record(current_user.id, DomainEvent.TARGET_CHANGED, affected_date=today)
    logger.info(
        "event=adaptive_target_applied user_id=%s previous=%s new=%s observed_maintenance=%s",
        current_user.id,
        previous,
        result.recommended_goal,
        result.observed_maintenance,
    )
    return _adaptive_response(await service.adaptive_target(current_user, today))


@router.post("/adaptive-target/dismiss", response_model=AdaptiveTargetResponse)
async def dismiss_adaptive_target(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdaptiveTargetResponse:
    """Keeps the current target and pauses suggestions for a cooldown."""
    current_user.adaptive_target_reviewed_at = dt.datetime.now(dt.UTC)
    await db.flush()
    return _adaptive_response(await WeightService(db).adaptive_target(current_user, client_today()))
