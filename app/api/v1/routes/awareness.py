import datetime as dt
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.user import User
from app.services.awareness_service import AwarenessService

logger = logging.getLogger("app.api.awareness")
router = APIRouter()


class AwarenessTodayResponse(BaseModel):
    has_logged_today: bool
    aware_today: bool
    aware_days_this_week: int
    days_in_week: int
    current_soft_streak: int
    last_aware_date: dt.date | None
    label: str


class AwarenessDay(BaseModel):
    date: dt.date
    is_aware: bool


class AwarenessWeekResponse(BaseModel):
    week_start: dt.date
    week_end: dt.date
    aware_days: int
    days: list[AwarenessDay]
    label: str


@router.get("/today", response_model=AwarenessTodayResponse)
async def get_awareness_today(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Soft awareness snapshot for the current day and week rhythm."""
    return await AwarenessService(db).today(current_user.id)


@router.get("/week", response_model=AwarenessWeekResponse)
async def get_awareness_week(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Per-day awareness rhythm for the current week (Mon–Sun)."""
    return await AwarenessService(db).week(current_user.id)
