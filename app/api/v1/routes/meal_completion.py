import datetime as dt
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas.meal_completion import MealCompletionRequest
from app.ai.schemas.meal_estimate import UserContext
from app.ai.services.calorie_estimation_service import AICalorieEstimationService
from app.dependencies.db import get_db
from app.dependencies.premium import require_premium_user
from app.models.user import User
from app.repositories.meal import MealRepository
from app.schemas.meal_completion import MealCompletionResponse, MealSuggestionResponse
from app.services.summary import SummaryService

logger = logging.getLogger("app.api.meal_completion")
router = APIRouter()

GUARDRAIL_MSG = {
    "en": {
        "summary": "You logged {consumed} kcal against today's {goal} kcal reference. You have {remaining} kcal remaining.",
        "note": "No additional meal suggestion is needed right now.",
    },
    "it": {
        "summary": "Hai registrato {consumed} kcal sul riferimento di {goal} kcal di oggi. Ti rimangono {remaining} kcal.",
        "note": "Per ora non serve un altro suggerimento di pasto.",
    },
    "es": {
        "summary": "Has registrado {consumed} kcal respecto a la referencia de {goal} kcal de hoy. Te quedan {remaining} kcal.",
        "note": "Por ahora no necesitas otra sugerencia de comida.",
    },
    "zh": {
        "summary": "您今天已记录 {consumed} 千卡，参考值为 {goal} 千卡，还剩 {remaining} 千卡。",
        "note": "目前不需要额外的餐食建议。",
    },
    "ja": {
        "summary": "今日の基準 {goal} kcal に対して {consumed} kcal を記録しました。残りは {remaining} kcal です。",
        "note": "今のところ追加の食事提案は必要ありません。",
    },
    "ar": {
        "summary": "سجلت {consumed} سعرة من مرجع اليوم البالغ {goal} سعرة. يتبقى لديك {remaining} سعرة.",
        "note": "لا تحتاج إلى اقتراح وجبة إضافية الآن.",
    },
}


def _macro_targets(user: User) -> tuple[int, int, int]:
    """Return explicit macro goals or the same defaults shown by the app."""
    calories = user.daily_calorie_goal if user.daily_calorie_goal > 0 else 2000
    if user.weight_kg is not None and user.weight_kg > 0:
        protein_per_kg = 1.2 if user.goal_type == "maintain" else 1.6
        protein_g = min(user.weight_kg * protein_per_kg, calories * 0.35 / 4)
    else:
        protein_g = calories * 0.20 / 4

    fat_g = calories * 0.30 / 9
    carbs_g = max((calories - protein_g * 4 - fat_g * 9) / 4, 0)
    return (
        (
            user.daily_protein_goal
            if user.daily_protein_goal is not None and user.daily_protein_goal > 0
            else round(protein_g)
        ),
        user.daily_carbs_goal if user.daily_carbs_goal is not None and user.daily_carbs_goal > 0 else round(carbs_g),
        user.daily_fat_goal if user.daily_fat_goal is not None and user.daily_fat_goal > 0 else round(fat_g),
    )


async def _build_user_context(db: AsyncSession, user: User, locale: str | None = None) -> UserContext:
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
        locale=locale,
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
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> MealCompletionResponse:
    """Suggests meals/recipes to complete the remaining calorie target for today."""
    today = dt.date.today()

    # Extract primary language code
    lang = "en"
    if accept_language:
        primary = accept_language.split(",")[0].split("-")[0].strip().lower()
        if primary in ["en", "it", "es", "zh", "ja", "ar"]:
            lang = primary

    # 1. Fetch / Sync today's summary
    summary_service = SummaryService(db)
    summary = await summary_service.sync_daily_summary(current_user.id, today)

    # 2. Fetch today's meals to aggregate macros and meal names
    meal_repo = MealRepository(db)
    meals = await meal_repo.get_user_meals_on_date(current_user.id, today)

    # Guardrail: remaining calories < 200
    if summary.remaining_calories < 200:
        remaining = max(summary.remaining_calories, 0)
        g_msg = GUARDRAIL_MSG.get(lang, GUARDRAIL_MSG["en"])
        return MealCompletionResponse(
            suggestions=[],
            daily_context_summary=g_msg["summary"].format(
                consumed=summary.consumed_calories,
                goal=current_user.daily_calorie_goal,
                remaining=remaining,
            ),
            macro_balance_note=g_msg["note"],
            remaining_calories=remaining,
            consumed_calories=summary.consumed_calories,
            daily_goal=current_user.daily_calorie_goal,
        )

    # Aggregate consumed macros
    consumed_protein = sum(m.total_protein_g or 0.0 for m in meals)
    consumed_carbs = sum(m.total_carbs_g or 0.0 for m in meals)
    consumed_fat = sum(m.total_fat_g or 0.0 for m in meals)
    meals_eaten_today = [m.meal_name for m in meals if m.meal_name]

    # 3. Build user context with parsed language code
    user_context = await _build_user_context(db, current_user, lang)

    # 4. Construct request for AI Service
    target_protein, target_carbs, target_fat = _macro_targets(current_user)
    completion_req = MealCompletionRequest(
        remaining_calories=summary.remaining_calories,
        consumed_calories=summary.consumed_calories,
        daily_goal=current_user.daily_calorie_goal,
        consumed_protein_g=consumed_protein,
        consumed_carbs_g=consumed_carbs,
        consumed_fat_g=consumed_fat,
        target_protein_g=target_protein,
        target_carbs_g=target_carbs,
        target_fat_g=target_fat,
        meals_eaten_today=meals_eaten_today,
    )

    # 5. Call AI service for suggestions
    ai_service = AICalorieEstimationService(db)
    ai_result = await ai_service.suggest_meal_completion(
        completion_req=completion_req,
        user_context=user_context,
        user_id=current_user.id,
    )

    valid_suggestions = [
        suggestion
        for suggestion in ai_result.suggestions
        if suggestion.meal_name.strip()
        and suggestion.ingredients
        and 0 < suggestion.estimated_calories <= summary.remaining_calories
    ]
    if not valid_suggestions:
        logger.warning(
            "Meal completion returned no usable suggestions for user_id=%s remaining=%s",
            current_user.id,
            summary.remaining_calories,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Meal suggestions could not be prepared. Please try again.",
        )

    # 6. Map validated alternatives to the response.
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
            for s in valid_suggestions
        ],
        daily_context_summary=ai_result.daily_context_summary,
        macro_balance_note=ai_result.macro_balance_note,
        remaining_calories=summary.remaining_calories,
        consumed_calories=summary.consumed_calories,
        daily_goal=current_user.daily_calorie_goal,
    )
