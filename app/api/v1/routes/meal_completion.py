import datetime as dt
import logging
from fastapi import APIRouter, Depends, status, Header
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

GUARDRAIL_MSG = {
    "en": {
        "summary": "You consumed {consumed} kcal out of a {goal} kcal goal. You have {remaining} kcal remaining. You have almost reached your goal for today!",
        "note": "No further meal suggestions are needed for today."
    },
    "it": {
        "summary": "Hai consumato {consumed} kcal su un obiettivo di {goal} kcal. Ti rimangono {remaining} kcal. Hai quasi raggiunto il tuo obiettivo per oggi!",
        "note": "Non sono necessari ulteriori suggerimenti di pasti per oggi."
    },
    "es": {
        "summary": "Has consumido {consumed} kcal de un objetivo de {goal} kcal. Te quedan {remaining} kcal. ¡Casi has alcanzado tu objetivo hoy!",
        "note": "No se necesitan más sugerencias de comidas para hoy."
    },
    "zh": {
        "summary": "您已摄入 {consumed} 千卡（目标为 {goal} 千卡）。您还剩 {remaining} 千卡。今天您已几乎达到目标！",
        "note": "今天不需要更多的膳食建议。"
    },
    "ja": {
        "summary": "目標 {goal} kcal のうち {consumed} kcal を摂取しました。残り {remaining} kcal です。今日の目標をほぼ達成しました！",
        "note": "今日の食事の提案はもう必要ありません。"
    },
    "ar": {
        "summary": "لقد استهلكت {consumed} سعرة حرارية من هدفك البالغ {goal} سعرة حرارية. يتبقى لديك {remaining} سعرة حرارية. لقد اقتربت من تحقيق هدفك اليوم!",
        "note": "لا حاجة لمزيد من اقتراحات الوجبات اليوم."
    }
}


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
    current_user: User = Depends(get_current_user),
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
        g_msg = GUARDRAIL_MSG.get(lang, GUARDRAIL_MSG["en"])
        return MealCompletionResponse(
            suggestions=[],
            daily_context_summary=g_msg["summary"].format(
                consumed=summary.consumed_calories,
                goal=current_user.daily_calorie_goal,
                remaining=summary.remaining_calories,
            ),
            macro_balance_note=g_msg["note"],
            remaining_calories=summary.remaining_calories,
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
