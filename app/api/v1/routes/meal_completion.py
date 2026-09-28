import datetime as dt
import hashlib
import logging

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas.meal_completion import MealCompletionRequest
from app.ai.schemas.meal_estimate import UserContext
from app.ai.services.calorie_estimation_service import AICalorieEstimationService
from app.dependencies.db import get_db
from app.dependencies.premium import require_premium_user
from app.models.user import User
from app.repositories.meal import MealRepository
from app.schemas.meal_completion import MealCompletionPreferences, MealCompletionResponse, MealSuggestionResponse
from app.schemas.meal_review import MealOccasionReviewResponse, MealReviewEvidence
from app.services.summary import SummaryService

logger = logging.getLogger("app.api.meal_completion")
router = APIRouter()

_CATEGORY_LABELS = {
    "en": {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "snack": "snack"},
    "it": {"breakfast": "colazione", "lunch": "pranzo", "dinner": "cena", "snack": "spuntino"},
    "es": {"breakfast": "desayuno", "lunch": "almuerzo", "dinner": "cena", "snack": "tentempié"},
    "zh": {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"},
    "ja": {"breakfast": "朝食", "lunch": "昼食", "dinner": "夕食", "snack": "間食"},
    "ar": {"breakfast": "الإفطار", "lunch": "الغداء", "dinner": "العشاء", "snack": "الوجبة الخفيفة"},
}

_EVIDENCE_LABELS = {
    "en": {
        "energy": "Energy logged",
        "entries": "Entries",
        "protein": "Protein",
        "carbohydrates": "Carbohydrates",
        "fat": "Fat",
    },
    "it": {
        "energy": "Energia registrata",
        "entries": "Voci",
        "protein": "Proteine",
        "carbohydrates": "Carboidrati",
        "fat": "Grassi",
    },
    "es": {
        "energy": "Energía registrada",
        "entries": "Entradas",
        "protein": "Proteínas",
        "carbohydrates": "Carbohidratos",
        "fat": "Grasas",
    },
    "zh": {
        "energy": "已记录能量",
        "entries": "记录数",
        "protein": "蛋白质",
        "carbohydrates": "碳水化合物",
        "fat": "脂肪",
    },
    "ja": {
        "energy": "記録したエネルギー",
        "entries": "記録数",
        "protein": "たんぱく質",
        "carbohydrates": "炭水化物",
        "fat": "脂質",
    },
    "ar": {
        "energy": "الطاقة المسجلة",
        "entries": "العناصر",
        "protein": "البروتين",
        "carbohydrates": "الكربوهيدرات",
        "fat": "الدهون",
    },
}

_REVIEW_COPY = {
    "en": {
        "one": "Your {category} contains one logged entry.",
        "many": "Your {category} combines {count} logged entries.",
        "macro": "Most recorded macro energy comes from {macro}.",
        "mixed": "Recorded macros are fairly mixed across this meal.",
        "suggestion": "If you want more variety next time, add a food from a group not already represented here.",
        "coverage": "Based only on confirmed items logged for this {category}; portions remain estimates.",
    },
    "it": {
        "one": "Il tuo {category} contiene una voce registrata.",
        "many": "Il tuo {category} unisce {count} voci registrate.",
        "macro": "La maggior parte dei macronutrienti registrati proviene da {macro}.",
        "mixed": "I macronutrienti registrati sono distribuiti in modo abbastanza vario.",
        "suggestion": "Se vuoi più varietà la prossima volta, aggiungi un alimento di un gruppo non ancora presente.",
        "coverage": "Basato solo sugli elementi confermati del {category}; le porzioni restano stime.",
    },
    "es": {
        "one": "Tu {category} contiene una entrada registrada.",
        "many": "Tu {category} combina {count} entradas registradas.",
        "macro": "La mayor parte de los macronutrientes registrados procede de {macro}.",
        "mixed": "Los macronutrientes registrados están bastante repartidos.",
        "suggestion": "Si quieres más variedad la próxima vez, añade un alimento de un grupo que aún no aparezca.",
        "coverage": "Basado solo en elementos confirmados de esta comida; las porciones siguen siendo estimaciones.",
    },
    "zh": {
        "one": "这次{category}包含一条已记录内容。",
        "many": "这次{category}包含 {count} 条已记录内容。",
        "macro": "记录的宏量营养中，{macro}占比最高。",
        "mixed": "这餐记录的宏量营养分布较均衡。",
        "suggestion": "如果下次想增加多样性，可以加入当前记录中没有的一类食物。",
        "coverage": "仅基于这次{category}中已确认的记录；份量仍是估算值。",
    },
    "ja": {
        "one": "この{category}には1件の記録があります。",
        "many": "この{category}には{count}件の記録があります。",
        "macro": "記録上もっとも多い栄養素は{macro}です。",
        "mixed": "記録された栄養素は比較的分散しています。",
        "suggestion": "次回さらに多様にしたい場合は、今回にない食品群を加えてみてください。",
        "coverage": "この{category}で確認済みの記録だけに基づきます。量は推定値です。",
    },
    "ar": {
        "one": "تحتوي وجبة {category} على عنصر واحد مسجل.",
        "many": "تجمع وجبة {category} عدد {count} من العناصر المسجلة.",
        "macro": "أكبر حصة من المغذيات المسجلة تأتي من {macro}.",
        "mixed": "المغذيات المسجلة موزعة بصورة متنوعة نسبيًا.",
        "suggestion": "للمزيد من التنوع لاحقًا، أضف طعامًا من مجموعة غير موجودة في السجل الحالي.",
        "coverage": "يعتمد على العناصر المؤكدة المسجلة في {category} فقط؛ وتبقى الحصص تقديرية.",
    },
}


def _language(accept_language: str | None) -> str:
    primary = (accept_language or "en").split(",", 1)[0].split("-", 1)[0].strip().lower()
    return primary if primary in _REVIEW_COPY else "en"


@router.get("/review/{category}", response_model=MealOccasionReviewResponse)
async def review_meal_occasion(
    category: str,
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> MealOccasionReviewResponse:
    if category not in {"breakfast", "lunch", "dinner", "snack"}:
        raise HTTPException(status_code=422, detail="Unsupported meal category.")
    meals = [
        meal
        for meal in await MealRepository(db).get_user_meals_on_date(current_user.id, dt.date.today())
        if meal.meal_category == category and meal.confirmed_calories is not None
    ]
    if not meals:
        raise HTTPException(status_code=404, detail="No confirmed meals to review.")

    meals.sort(key=lambda meal: meal.id)
    meal_ids = [meal.id for meal in meals]
    signature = "|".join(
        f"{meal.id}:{meal.confirmed_calories}:{meal.updated_at.isoformat() if meal.updated_at else ''}"
        for meal in meals
    )
    fingerprint = hashlib.sha256(signature.encode()).hexdigest()[:24]
    total_calories = sum(meal.confirmed_calories or 0 for meal in meals)
    macros = {
        "protein": sum(meal.total_protein_g or 0 for meal in meals),
        "carbohydrates": sum(meal.total_carbs_g or 0 for meal in meals),
        "fat": sum(meal.total_fat_g or 0 for meal in meals),
    }
    lang = _language(accept_language)
    copy = _REVIEW_COPY[lang]
    label = _CATEGORY_LABELS[lang][category]
    known_macros = sum(macros.values()) > 0
    macro_energy = {
        "protein": macros["protein"] * 4,
        "carbohydrates": macros["carbohydrates"] * 4,
        "fat": macros["fat"] * 9,
    }
    dominant = max(macro_energy, key=macro_energy.get) if known_macros else None
    labels = _EVIDENCE_LABELS[lang]
    localized_macro = labels.get(dominant or "", "")
    opening = copy["one"] if len(meals) == 1 else copy["many"]
    observation = opening.format(category=label, count=len(meals))
    observation += " " + (copy["macro"].format(macro=localized_macro) if known_macros else copy["mixed"])
    evidence = [
        MealReviewEvidence(label=labels["energy"], value=f"{total_calories} kcal"),
        MealReviewEvidence(label=labels["entries"], value=str(len(meals))),
    ]
    if known_macros:
        evidence.extend(
            MealReviewEvidence(label=labels[name], value=f"{value:.0f} g")
            for name, value in macros.items()
            if value > 0
        )
    return MealOccasionReviewResponse(
        category=category,
        meal_ids=meal_ids,
        fingerprint=fingerprint,
        meal_count=len(meals),
        total_calories=total_calories,
        protein_g=macros["protein"] if known_macros else None,
        carbs_g=macros["carbohydrates"] if known_macros else None,
        fat_g=macros["fat"] if known_macros else None,
        observation=observation,
        suggestion=copy["suggestion"]
        if len({item.name.lower() for meal in meals for item in meal.items}) < 4
        else None,
        coverage_note=copy["coverage"].format(category=label),
        evidence=evidence,
    )


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
    preferences: MealCompletionPreferences | None = Body(default=None),
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> MealCompletionResponse:
    """Suggests meals/recipes to complete the remaining calorie target for today."""
    today = dt.date.today()
    preferences = preferences or MealCompletionPreferences()

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
        requested_meal_type=preferences.meal_type,
        max_prep_minutes=preferences.max_prep_minutes,
        dietary_preference=preferences.dietary_preference,
        available_ingredients=[item.strip() for item in preferences.available_ingredients if item.strip()],
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
        and (preferences.meal_type is None or suggestion.meal_type == preferences.meal_type)
        and (preferences.max_prep_minutes is None or suggestion.prep_time_minutes <= preferences.max_prep_minutes)
        and (preferences.dietary_preference == "any" or preferences.dietary_preference in suggestion.dietary_tags)
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
