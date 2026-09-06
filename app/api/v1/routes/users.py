import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.insights.versioning import DomainEvent, InsightVersionService
from app.models.user import User
from app.models.meal import Meal
from app.repositories.user import UserRepository
from app.schemas.user import UserResponse, UserUpdate
from app.services.summary import SummaryService
from app.services.calorie_target_service import CalorieTargetService

logger = logging.getLogger("app.api.users")
router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def read_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Returns the authenticated user's profile details."""
    result = UserResponse.model_validate(current_user).model_dump()
    result["has_confirmed_meals"] = await db.scalar(
        select(Meal.id).where(Meal.user_id == current_user.id, Meal.confirmed_calories.is_not(None)).limit(1)
    ) is not None
    return result


@router.patch("/me", response_model=UserResponse)
async def update_user_profile(
    profile_update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Updates user metadata (e.g. daily calorie goals, target plans, display name).

    If the calorie target is modified, automatically synchronizes today's
    DailySummary to match the updated goal.
    """
    user_repo = UserRepository(db)
    explicit_target = profile_update.daily_calorie_goal is not None
    original_goal = current_user.daily_calorie_goal
    original_values = {
        field: getattr(current_user, field)
        for field in UserUpdate.model_fields if hasattr(current_user, field)
    }

    # Check if we should automatically estimate/update the calorie target
    sex = profile_update.sex if profile_update.sex is not None else current_user.sex
    age = profile_update.age if profile_update.age is not None else current_user.age
    height_cm = profile_update.height_cm if profile_update.height_cm is not None else current_user.height_cm
    weight_kg = profile_update.weight_kg if profile_update.weight_kg is not None else current_user.weight_kg
    goal_type = profile_update.goal_type if profile_update.goal_type is not None else current_user.goal_type

    nutritional_fields = {"sex", "age", "height_cm", "weight_kg", "goal_type"}
    nutrition_changed = any(
        field in profile_update.model_fields_set
        and getattr(profile_update, field) is not None
        and getattr(profile_update, field) != original_values[field]
        for field in nutritional_fields
    )
    if profile_update.daily_calorie_goal is None and nutrition_changed and current_user.calorie_target_source != "user_adjusted":
        if sex and age is not None and height_cm is not None and weight_kg is not None and goal_type:
            bmr = CalorieTargetService.calculate_bmr(
                weight_kg=weight_kg,
                height_cm=height_cm,
                age=age,
                sex=sex,
            )
            maintenance = CalorieTargetService.calculate_maintenance_calories(bmr, current_user.activity_level or "light")
            estimated_goal = CalorieTargetService.calculate_daily_target(maintenance, goal_type, current_user.target_pace or "balanced")
            profile_update.daily_calorie_goal = estimated_goal

    if profile_update.daily_calorie_goal is not None:
        suggested = original_goal
        if sex and age is not None and height_cm is not None and weight_kg is not None:
            bmr = CalorieTargetService.calculate_bmr(weight_kg, height_cm, age, sex)
            maintenance = CalorieTargetService.calculate_maintenance_calories(bmr, current_user.activity_level or "light")
            suggested = CalorieTargetService.calculate_daily_target(maintenance, goal_type, current_user.target_pace or "balanced")
        if CalorieTargetService.requires_confirmation(profile_update.daily_calorie_goal, sex, suggested) and not profile_update.unsafe_target_confirmed:
            raise HTTPException(status_code=422, detail={"code": "TARGET_CONFIRMATION_REQUIRED"})
        if explicit_target:
            current_user.calorie_target_source = "user_adjusted"

    # Update profile in-place
    updated_user = await user_repo.update(current_user, profile_update)

    # If the user adjusted their daily target calories, trigger today's summary sync
    if profile_update.daily_calorie_goal is not None and profile_update.daily_calorie_goal != original_goal:
        try:
            today = dt.date.today()
            summary_service = SummaryService(db)
            await summary_service.sync_daily_summary(updated_user.id, today)
            logger.info(f"Recalculated summary for user_id={updated_user.id} following calorie goal update.")
        except Exception as e:
            logger.error(f"Failed to sync today's summary after user target modification: {e}")

    changed_fields = {
        field
        for field, old_value in original_values.items()
        if getattr(updated_user, field) != old_value
    }
    target_fields = {
        "daily_calorie_goal",
        "goal_type",
        "daily_protein_goal",
        "daily_carbs_goal",
        "daily_fat_goal",
    }
    events: list[DomainEvent] = []
    if changed_fields.intersection(target_fields):
        events.append(DomainEvent.TARGET_CHANGED)
    if "weight_kg" in changed_fields:
        events.append(DomainEvent.WEIGHT_UPDATED)
    if changed_fields - target_fields - {"weight_kg"}:
        events.append(DomainEvent.PROFILE_CHANGED)
    if events:
        await InsightVersionService(db).record(updated_user.id, *events, affected_date=dt.date.today())

    return await read_current_user_profile(updated_user, db)


class FCMTokenUpdate(BaseModel):
    token: str = Field(..., min_length=10, max_length=512)


@router.post("/me/fcm-token", status_code=status.HTTP_204_NO_CONTENT)
async def update_fcm_token(
    payload: FCMTokenUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Stores the device FCM token and revives recent notification candidates."""
    current_user.fcm_token = payload.token
    await db.flush()
    from app.proactive_insights.notifications import InsightNotificationService

    await InsightNotificationService(db).reschedule_after_token(
        current_user, now=dt.datetime.now(dt.UTC)
    )


@router.delete("/me/fcm-token", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fcm_token(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    current_user.fcm_token = None
    await db.flush()
