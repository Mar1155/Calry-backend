import datetime as dt
import logging
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.services.calorie_estimation_service import AICalorieEstimationService
from app.ai.schemas.meal_completion import MealCompletionRequest
from app.ai.schemas.meal_estimate import UserContext
from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.user import User
from app.repositories.meal import MealRepository
from app.schemas.meal_completion import MealCompletionResponse, MealSuggestionResponse
from app.services.summary import SummaryService

logger = logging.getLogger("app.api.meal_completion")
router = APIRouter()


async def _build_user_context(db: AsyncSession, user: User) -> UserContext:
    """Retrieves calibration/correction history and profile to build a comprehensive UserContext."""
    from app.ai.services.correction_context_service import AICorrectionContextService

    try:
        correction_service = AICorrectionContextService(db)
        summary = await correction_service.get_user_correction_summary(user.id)
        avg_pct = await correction_service.get_average_correction_percent(user.id)
    except Exception as e:
        logger.warning(f"Could not load correction context: {e}")
        summary = None
        avg_pct = None

    return UserContext(
        daily_calorie_goal=user.daily_calorie_goal,
        locale=None,
        timezone=None,
        previous_corrections_summary=summary,
        sex=user.sex,
        age=user.age,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        goal_type=user.goal_type,
        avg_correction_percent=avg_pct,
    )


@router.post("/complete-day", response_model=MealCompletionResponse)
async def suggest_daily_completion(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MealCompletionResponse:
    """Suggests meals/recipes to complete the remaining calorie target for today."""
    today = dt.date.today()

    # 1. Fetch / Sync today's summary
    summary_service = SummaryService(db)
    summary = await summary_service.sync_daily_summary(current_user.id, today)

    # 2. Fetch today's meals to aggregate macros and meal names
    meal_repo = MealRepository(db)
    meals = await meal_repo.get_user_meals_on_date(current_user.id, today)

    # Guardrail: remaining calories < 200
    if summary.remaining_calories < 200:
        return MealCompletionResponse(
            suggestions=[],
            daily_context_summary=(
                f"Hai consumato {summary.consumed_calories} kcal su un obiettivo di "
                f"{current_user.daily_calorie_goal} kcal. Ti rimangono {summary.remaining_calories} kcal. "
                "Hai quasi raggiunto il tuo obiettivo per oggi!"
            ),
            macro_balance_note="Non sono necessari ulteriori suggerimenti di pasti per oggi.",
            remaining_calories=summary.remaining_calories,
            consumed_calories=summary.consumed_calories,
            daily_goal=current_user.daily_calorie_goal,
        )

    # Aggregate consumed macros
    consumed_protein = sum(m.total_protein_g or 0.0 for m in meals)
    consumed_carbs = sum(m.total_carbs_g or 0.0 for m in meals)
    consumed_fat = sum(m.total_fat_g or 0.0 for m in meals)
    meals_eaten_today = [m.meal_name for m in meals if m.meal_name]

    # 3. Build user context
    user_context = await _build_user_context(db, current_user)

    # 4. Construct request for AI Service
    completion_req = MealCompletionRequest(
        remaining_calories=summary.remaining_calories,
        consumed_calories=summary.consumed_calories,
        daily_goal=current_user.daily_calorie_goal,
        consumed_protein_g=consumed_protein,
        consumed_carbs_g=consumed_carbs,
        consumed_fat_g=consumed_fat,
        meals_eaten_today=meals_eaten_today,
    )

    # 5. Call AI service for suggestions
    ai_service = AICalorieEstimationService(db)
    ai_result = await ai_service.suggest_meal_completion(
        completion_req=completion_req,
        user_context=user_context,
        user_id=current_user.id,
    )

    # 6. Map to Response Schema
    return MealCompletionResponse(
        suggestions=[
            MealSuggestionResponse(
                meal_name=s.meal_name,
                description=s.description,
                estimated_calories=s.estimated_calories,
                protein_g=s.protein_g,
                carbs_g=s.carbs_g,
                fat_g=s.fat_g,
                ingredients=s.ingredients,
                preparation_hint=s.preparation_hint,
                reasoning=s.reasoning,
                meal_type=s.meal_type,
                difficulty=s.difficulty,
                prep_time_minutes=s.prep_time_minutes,
            )
            for s in ai_result.suggestions
        ],
        daily_context_summary=ai_result.daily_context_summary,
        macro_balance_note=ai_result.macro_balance_note,
        remaining_calories=summary.remaining_calories,
        consumed_calories=summary.consumed_calories,
        daily_goal=current_user.daily_calorie_goal,
    )
