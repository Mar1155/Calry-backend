import datetime as dt

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.insights.versioning import DomainEvent, InsightVersionService
from app.models.user import User
from app.schemas.onboarding import CalculateTargetRequest, CompleteOnboardingRequest, OnboardingStatusResponse
from app.services.calorie_target_service import CalorieTargetService
from app.services.summary import SummaryService

router = APIRouter()


def _pace(payload: CalculateTargetRequest | CompleteOnboardingRequest) -> str:
    return payload.target_pace or "balanced"


def _calculation(payload: CalculateTargetRequest | CompleteOnboardingRequest) -> dict:
    pace = _pace(payload)
    if payload.goal_type == "maintain":
        pace = "balanced"
    bmr = CalorieTargetService.calculate_bmr(payload.weight_kg, payload.height_cm, payload.age, payload.formula_profile)
    maintenance = CalorieTargetService.calculate_maintenance_calories(bmr, payload.activity_level)
    target = CalorieTargetService.calculate_daily_target(maintenance, payload.goal_type, pace)
    return {"bmr": round(bmr), "maintenance_calories": round(maintenance), "suggested_target": target, "rounded_target": target, "pace": pace}


@router.get("/status", response_model=OnboardingStatusResponse)
async def onboarding_status(current_user: User = Depends(get_current_user)) -> dict:
    return {"status": current_user.onboarding_status, "current_step": current_user.onboarding_step, "version": current_user.onboarding_version, "completed_at": current_user.onboarding_completed_at}


@router.post("/calculate-target")
async def calculate_target(payload: CalculateTargetRequest, current_user: User = Depends(get_current_user)) -> dict:
    result = _calculation(payload)
    return {**result, "calculation_version": "mifflin_activity_v2", "explanation": {"goal": payload.goal_type, "pace": result["pace"], "activity_level": payload.activity_level}}


@router.post("/complete")
async def complete_onboarding(payload: CompleteOnboardingRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    if payload.owner_uid is not None and payload.owner_uid != current_user.firebase_uid:
        raise HTTPException(status_code=409, detail="Account changed. Review your draft.")
    # Serialize completion per account. A lost response/retry never overwrites
    # a completed profile, resets its offer, or repeats insight side effects.
    current_user = (await db.execute(select(User).where(User.id == current_user.id).with_for_update().execution_options(populate_existing=True))).scalar_one()
    if current_user.onboarding_status == "completed":
        return {"status": "completed", "daily_calorie_goal": current_user.daily_calorie_goal}
    if payload.journey_id and await db.scalar(select(User.id).where(User.onboarding_journey_id == payload.journey_id, User.id != current_user.id)):
        raise HTTPException(status_code=409, detail="Journey already completed.")
    result = _calculation(payload)
    if CalorieTargetService.requires_confirmation(payload.selected_target, payload.formula_profile, result["suggested_target"]) and not payload.unsafe_target_confirmed:
        raise HTTPException(status_code=422, detail={"code": "TARGET_CONFIRMATION_REQUIRED"})

    # Single transaction: no profile writes occur before this endpoint.
    now = dt.datetime.now(dt.UTC)
    current_user.goal_type = payload.goal_type
    current_user.sex = payload.formula_profile
    current_user.age = payload.age
    current_user.height_cm = payload.height_cm
    current_user.weight_kg = payload.weight_kg
    current_user.activity_level = payload.activity_level
    current_user.target_pace = result["pace"]
    current_user.preferred_unit_system = payload.preferred_unit_system
    current_user.daily_calorie_goal = payload.selected_target
    current_user.calorie_target_source = "user_adjusted" if payload.selected_target != result["suggested_target"] else "calculated"
    current_user.onboarding_status = "completed"
    current_user.onboarding_step = None
    current_user.onboarding_version = payload.onboarding_version
    started = payload.started_at
    if started is not None:
        started = started.replace(tzinfo=dt.UTC) if started.tzinfo is None else started.astimezone(dt.UTC)
        started = min(now, max(now - dt.timedelta(days=30), started))
    current_user.onboarding_started_at = current_user.onboarding_started_at or started or now
    current_user.onboarding_completed_at = now
    current_user.onboarding_offer_status = "pending" if payload.onboarding_version >= 3 and not current_user.is_premium else "handled"
    current_user.onboarding_journey_id = payload.journey_id
    db.add(current_user)
    await db.flush()
    await SummaryService(db).sync_daily_summary(current_user.id, dt.date.today())
    await InsightVersionService(db).record(
        current_user.id,
        DomainEvent.PROFILE_CHANGED,
        DomainEvent.TARGET_CHANGED,
        DomainEvent.WEIGHT_UPDATED,
        affected_date=dt.date.today(),
    )
    return {"status": "completed", "daily_calorie_goal": current_user.daily_calorie_goal, "goal_type": current_user.goal_type, "activity_level": current_user.activity_level, "target_pace": current_user.target_pace, "calorie_target_source": current_user.calorie_target_source, "onboarding_completed_at": current_user.onboarding_completed_at}


@router.post("/offer/handled", status_code=status.HTTP_204_NO_CONTENT)
async def mark_offer_handled(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db), journey_id: str | None = Body(default=None, embed=True)) -> None:
    if current_user.onboarding_status != "completed":
        raise HTTPException(status_code=409, detail="Complete onboarding first.")
    if journey_id is not None and journey_id != current_user.onboarding_journey_id:
        raise HTTPException(status_code=409, detail="Account changed.")
    current_user.onboarding_offer_status = "handled"
    await db.flush()


# This endpoint accepts only low-trust interaction telemetry, never purchase or
# completion assertions. Business outcomes come from users/meals/RevenueCat.
from collections import OrderedDict
from time import monotonic
from fastapi import Request
from sqlalchemy import delete
from app.models.onboarding_event import OnboardingEvent
from app.schemas.onboarding_event import OnboardingEventBatch

_event_windows: OrderedDict[str, tuple[float, int]] = OrderedDict()


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
async def record_onboarding_events(payload: OnboardingEventBatch, request: Request, db: AsyncSession = Depends(get_db)) -> None:
    peer = request.client.host if request.client else "unknown"
    now_tick = monotonic()
    start, count = _event_windows.get(peer, (now_tick, 0))
    if now_tick - start >= 60:
        start, count = now_tick, 0
    if count >= 60:
        raise HTTPException(status_code=429, detail="Try later.")
    _event_windows[peer] = (start, count + 1)
    _event_windows.move_to_end(peer)
    while len(_event_windows) > 4096:
        _event_windows.popitem(last=False)
    now = dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(days=30)
    rows = []
    for event in payload.events:
        occurred = event.occurred_at
        occurred = occurred.replace(tzinfo=dt.UTC) if occurred.tzinfo is None else occurred.astimezone(dt.UTC)
        if occurred < cutoff or occurred > now + dt.timedelta(minutes=5):
            continue
        rows.append({**event.model_dump(), "occurred_at": min(occurred, now), "received_at": now})
    if rows:
        if db.bind.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        await db.execute(insert(OnboardingEvent).values(rows).on_conflict_do_nothing(index_elements=["event_id"]))
    await db.execute(delete(OnboardingEvent).where(OnboardingEvent.received_at < cutoff))
