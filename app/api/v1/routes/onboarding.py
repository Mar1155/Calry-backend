import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
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


def _minimum_target(profile: str) -> int:
    # Product guardrail, not medical advice.
    return 1500 if profile == "male" else 1200


@router.get("/status", response_model=OnboardingStatusResponse)
async def onboarding_status(current_user: User = Depends(get_current_user)) -> dict:
    return {"status": current_user.onboarding_status, "current_step": current_user.onboarding_step, "version": current_user.onboarding_version, "completed_at": current_user.onboarding_completed_at}


@router.post("/calculate-target")
async def calculate_target(payload: CalculateTargetRequest, current_user: User = Depends(get_current_user)) -> dict:
    result = _calculation(payload)
    return {**result, "calculation_version": "mifflin_activity_v2", "explanation": {"goal": payload.goal_type, "pace": result["pace"], "activity_level": payload.activity_level}}


@router.post("/complete")
async def complete_onboarding(payload: CompleteOnboardingRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    result = _calculation(payload)
    minimum = _minimum_target(payload.formula_profile)
    far_below_estimate = payload.selected_target < result["suggested_target"] - 500
    if (payload.selected_target < minimum or far_below_estimate) and not payload.unsafe_target_confirmed:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "TARGET_CONFIRMATION_REQUIRED", "message": "Questo target è molto più basso della stima iniziale. Conferma per continuare."})

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
    current_user.calorie_target_source = "user_adjusted" if payload.target_was_manually_adjusted else "calculated"
    current_user.onboarding_status = "completed"
    current_user.onboarding_step = None
    current_user.onboarding_version = payload.onboarding_version
    current_user.onboarding_started_at = current_user.onboarding_started_at or now
    current_user.onboarding_completed_at = now
    db.add(current_user)
    await db.flush()
    await SummaryService(db).sync_daily_summary(current_user.id, dt.date.today())
    return {"status": "completed", "daily_calorie_goal": current_user.daily_calorie_goal, "goal_type": current_user.goal_type, "activity_level": current_user.activity_level, "target_pace": current_user.target_pace, "calorie_target_source": current_user.calorie_target_source, "onboarding_completed_at": current_user.onboarding_completed_at}
