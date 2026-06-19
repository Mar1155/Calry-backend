import datetime as dt
import logging

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserResponse, UserUpdate
from app.services.summary import SummaryService

logger = logging.getLogger("app.api.users")
router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def read_current_user_profile(
    current_user: User = Depends(get_current_user),
) -> User:
    """Returns the authenticated user's profile details."""
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_user_profile(
    profile_update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Updates user metadata (e.g. daily calorie goals, target plans, display name).

    If the calorie target is modified, automatically synchronizes today's
    DailySummary to match the updated goal.
    """
    user_repo = UserRepository(db)
    original_goal = current_user.daily_calorie_goal

    # Check if we should automatically estimate/update the calorie target
    sex = profile_update.sex if profile_update.sex is not None else current_user.sex
    age = profile_update.age if profile_update.age is not None else current_user.age
    height_cm = profile_update.height_cm if profile_update.height_cm is not None else current_user.height_cm
    weight_kg = profile_update.weight_kg if profile_update.weight_kg is not None else current_user.weight_kg
    goal_type = profile_update.goal_type if profile_update.goal_type is not None else current_user.goal_type

    if profile_update.daily_calorie_goal is None:
        if sex and age is not None and height_cm is not None and weight_kg is not None and goal_type:
            from app.services.calorie_target_service import CalorieTargetService
            bmr = CalorieTargetService.calculate_bmr(
                weight_kg=weight_kg,
                height_cm=height_cm,
                age=age,
                sex=sex,
            )
            maintenance = CalorieTargetService.calculate_maintenance_calories(bmr)
            estimated_goal = CalorieTargetService.calculate_daily_target(maintenance, goal_type)
            profile_update.daily_calorie_goal = estimated_goal

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

    return updated_user


class FCMTokenUpdate(BaseModel):
    token: str = Field(..., min_length=10, max_length=512)

@router.post("/me/fcm-token", status_code=status.HTTP_204_NO_CONTENT)
async def update_fcm_token(
    payload: FCMTokenUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Stores the device FCM push token for future push notification support."""
    current_user.fcm_token = payload.token
    await db.flush()
