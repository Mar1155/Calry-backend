import hashlib
import logging

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas.meal_completion import MealCompletionRequest
from app.ai.schemas.meal_estimate import UserContext
from app.ai.schemas.meal_review import DayReviewMealFact, DayReviewRequest, MealOccasionReviewRequest
from app.ai.services.calorie_estimation_service import AICalorieEstimationService
from app.core.client_time import client_today
from app.dependencies.db import get_db
from app.dependencies.premium import require_premium_user
from app.models.user import User
from app.repositories.meal import MealRepository
from app.schemas.meal_completion import MealCompletionPreferences, MealCompletionResponse, MealSuggestionResponse
from app.schemas.meal_review import DayReviewMeal, DayReviewResponse, MealOccasionReviewResponse, MealReviewEvidence
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
    "de": {"breakfast": "Frühstück", "lunch": "Mittagessen", "dinner": "Abendessen", "snack": "Snack"},
    "fr": {"breakfast": "petit-déjeuner", "lunch": "déjeuner", "dinner": "dîner", "snack": "collation"},
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
    "de": {
        "energy": "Erfasste Energie",
        "entries": "Einträge",
        "protein": "Protein",
        "carbohydrates": "Kohlenhydrate",
        "fat": "Fett",
    },
    "fr": {
        "energy": "Énergie enregistrée",
        "entries": "Entrées",
        "protein": "Protéines",
        "carbohydrates": "Glucides",
        "fat": "Lipides",
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
    "de": {
        "one": "Für {category} ist ein Eintrag erfasst.",
        "many": "Für {category} sind {count} Einträge erfasst.",
        "macro": "Die meiste erfasste Makro-Energie stammt aus {macro}.",
        "mixed": "Die erfassten Makros sind in dieser Mahlzeit recht ausgewogen verteilt.",
        "suggestion": "Wenn du beim nächsten Mal mehr Abwechslung möchtest, füge ein Lebensmittel aus einer Gruppe hinzu, die hier noch fehlt.",
        "coverage": "Nur auf Basis der bestätigten Einträge für {category}; Portionen bleiben Schätzungen.",
    },
    "fr": {
        "one": "Ce repas ({category}) contient une entrée enregistrée.",
        "many": "Ce repas ({category}) regroupe {count} entrées enregistrées.",
        "macro": "Macro dominante dans ce repas : {macro}.",
        "mixed": "Les macros enregistrées sont plutôt équilibrées dans ce repas.",
        "suggestion": "Pour plus de variété la prochaine fois, ajoute un aliment d’un groupe qui n’est pas encore représenté.",
        "coverage": "Basé uniquement sur les éléments confirmés de ce repas ({category}) ; les portions restent des estimations.",
    },
}


_DAY_REVIEW_COPY = {
    "en": {
        "headline_one": "You logged {count} meal today, {calories} kcal so far.",
        "headline_many": "You logged {count} meals today, {calories} kcal so far.",
        "standout": "{meal} was the largest entry today at {calories} kcal.",
        "macro": "Most recorded macro energy today comes from {macro}.",
        "mixed": "Today's recorded macros are fairly mixed.",
        "tip": "If you want more variety tomorrow, try a food group not already represented today.",
        "coverage": "Based only on confirmed meals logged today; portions remain estimates.",
    },
    "it": {
        "headline_one": "Hai registrato {count} pasto oggi, {calories} kcal finora.",
        "headline_many": "Hai registrato {count} pasti oggi, {calories} kcal finora.",
        "standout": "{meal} è stata la voce più corposa di oggi, con {calories} kcal.",
        "macro": "La maggior parte dei macronutrienti di oggi proviene da {macro}.",
        "mixed": "I macronutrienti di oggi sono distribuiti in modo abbastanza vario.",
        "tip": "Se vuoi più varietà domani, prova un gruppo alimentare non ancora presente oggi.",
        "coverage": "Basato solo sui pasti confermati registrati oggi; le porzioni restano stime.",
    },
    "es": {
        "headline_one": "Registraste {count} comida hoy, {calories} kcal hasta ahora.",
        "headline_many": "Registraste {count} comidas hoy, {calories} kcal hasta ahora.",
        "standout": "{meal} fue la entrada más grande de hoy, con {calories} kcal.",
        "macro": "La mayor parte de los macronutrientes de hoy procede de {macro}.",
        "mixed": "Los macronutrientes de hoy están bastante repartidos.",
        "tip": "Si quieres más variedad mañana, prueba un grupo de alimentos que hoy no aparezca.",
        "coverage": "Basado solo en comidas confirmadas registradas hoy; las porciones siguen siendo estimaciones.",
    },
    "zh": {
        "headline_one": "你今天记录了 {count} 餐，目前共 {calories} 千卡。",
        "headline_many": "你今天记录了 {count} 餐，目前共 {calories} 千卡。",
        "standout": "{meal}是今天份量最大的一餐，{calories} 千卡。",
        "macro": "今天记录的宏量营养中，{macro}占比最高。",
        "mixed": "今天记录的宏量营养分布较均衡。",
        "tip": "如果明天想增加多样性，可以尝试今天没有的一类食物。",
        "coverage": "仅基于今天已确认的记录；份量仍是估算值。",
    },
    "ja": {
        "headline_one": "今日は{count}件の食事を記録し、現在{calories} kcalです。",
        "headline_many": "今日は{count}件の食事を記録し、現在{calories} kcalです。",
        "standout": "今日最も量が多かったのは{meal}で、{calories} kcalでした。",
        "macro": "今日記録上もっとも多い栄養素は{macro}です。",
        "mixed": "今日記録された栄養素は比較的分散しています。",
        "tip": "明日さらに多様にしたい場合は、今日にない食品群を加えてみてください。",
        "coverage": "今日確認済みの記録だけに基づきます。量は推定値です。",
    },
    "ar": {
        "headline_one": "سجلت {count} وجبة اليوم، بإجمالي {calories} سعرة حتى الآن.",
        "headline_many": "سجلت {count} وجبات اليوم، بإجمالي {calories} سعرة حتى الآن.",
        "standout": "كانت {meal} أكبر عنصر اليوم بـ {calories} سعرة.",
        "macro": "أكبر حصة من المغذيات اليوم تأتي من {macro}.",
        "mixed": "المغذيات المسجلة اليوم موزعة بصورة متنوعة نسبيًا.",
        "tip": "للمزيد من التنوع غدًا، جرّب مجموعة طعام غير موجودة اليوم.",
        "coverage": "يعتمد فقط على الوجبات المؤكدة المسجلة اليوم؛ وتبقى الحصص تقديرية.",
    },
    "de": {
        "headline_one": "Du hast heute {count} Mahlzeit erfasst, bisher {calories} kcal.",
        "headline_many": "Du hast heute {count} Mahlzeiten erfasst, bisher {calories} kcal.",
        "standout": "{meal} war heute der größte Eintrag mit {calories} kcal.",
        "macro": "Die meiste erfasste Makro-Energie stammt heute aus {macro}.",
        "mixed": "Die heute erfassten Makros sind recht ausgewogen verteilt.",
        "tip": "Wenn du morgen mehr Abwechslung möchtest, probiere eine Lebensmittelgruppe, die heute noch fehlt.",
        "coverage": "Nur auf Basis der heute bestätigten Mahlzeiten; Portionen bleiben Schätzungen.",
    },
    "fr": {
        "headline_one": "Tu as enregistré {count} repas aujourd’hui, {calories} kcal pour l’instant.",
        "headline_many": "Tu as enregistré {count} repas aujourd’hui, {calories} kcal pour l’instant.",
        "standout": "{meal} a été l’entrée la plus importante aujourd’hui, avec {calories} kcal.",
        "macro": "Macro dominante aujourd’hui : {macro}.",
        "mixed": "Les macros enregistrées aujourd’hui sont plutôt équilibrées.",
        "tip": "Pour plus de variété demain, essaie un groupe d’aliments qui n’apparaît pas encore aujourd’hui.",
        "coverage": "Basé uniquement sur les repas confirmés aujourd’hui ; les portions restent des estimations.",
    },
}

_MACRO_TAGS = {
    "protein": "protein-rich",
    "carbohydrates": "carb-forward",
    "fat": "fat-forward",
}


def _language(accept_language: str | None) -> str:
    primary = (accept_language or "en").split(",", 1)[0].split("-", 1)[0].strip().lower()
    return primary if primary in _REVIEW_COPY else "en"


def _meal_macros(meals: list) -> tuple[dict[str, float], bool, str | None]:
    macros = {
        "protein": sum(meal.total_protein_g or 0 for meal in meals),
        "carbohydrates": sum(meal.total_carbs_g or 0 for meal in meals),
        "fat": sum(meal.total_fat_g or 0 for meal in meals),
    }
    known_macros = sum(macros.values()) > 0
    macro_energy = {
        "protein": macros["protein"] * 4,
        "carbohydrates": macros["carbohydrates"] * 4,
        "fat": macros["fat"] * 9,
    }
    dominant = max(macro_energy, key=macro_energy.get) if known_macros else None
    return macros, known_macros, dominant


@router.get("/review/day", response_model=DayReviewResponse)
async def review_day(
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> DayReviewResponse:
    today = client_today()
    meal_repo = MealRepository(db)
    meals = [
        meal
        for meal in await meal_repo.get_user_meals_on_date(current_user.id, today)
        if meal.confirmed_calories is not None
    ]
    if not meals:
        raise HTTPException(status_code=404, detail="No confirmed meals to review.")

    meals.sort(key=lambda meal: meal.id)
    signature = "|".join(
        f"{meal.id}:{meal.confirmed_calories}:{meal.updated_at.isoformat() if meal.updated_at else ''}"
        for meal in meals
    )
    fingerprint = hashlib.sha256(signature.encode()).hexdigest()[:24]
    total_calories = sum(meal.confirmed_calories or 0 for meal in meals)
    macros, known_macros, dominant = _meal_macros(meals)
    lang = _language(accept_language)
    copy = _DAY_REVIEW_COPY.get(lang, _DAY_REVIEW_COPY["en"])
    labels = _EVIDENCE_LABELS[lang]
    localized_macro = labels.get(dominant or "", "")
    category_labels = _CATEGORY_LABELS[lang]

    summary_service = SummaryService(db)
    summary = await summary_service.sync_daily_summary(current_user.id, today)

    largest_meal = max(meals, key=lambda meal: meal.confirmed_calories or 0)
    fallback_headline_key = "headline_one" if len(meals) == 1 else "headline_many"
    fallback_headline = copy[fallback_headline_key].format(count=len(meals), calories=total_calories)
    fallback_standout = copy["standout"].format(
        meal=largest_meal.meal_name or category_labels.get(largest_meal.meal_category, largest_meal.meal_category),
        calories=largest_meal.confirmed_calories or 0,
    )
    fallback_balance = copy["macro"].format(macro=localized_macro) if known_macros else copy["mixed"]
    fallback_tip = copy["tip"]
    coverage_note = copy["coverage"]

    headline, standout, balance_note, tip = fallback_headline, fallback_standout, fallback_balance, fallback_tip
    try:
        user_context = await _build_user_context(db, current_user, accept_language)
        target_protein, target_carbs, target_fat = _macro_targets(current_user)
        ai_service = AICalorieEstimationService(db)
        ai_result = await ai_service.review_day(
            DayReviewRequest(
                meals=[
                    DayReviewMealFact(
                        name=meal.meal_name or category_labels.get(meal.meal_category, meal.meal_category),
                        category=meal.meal_category,
                        calories=meal.confirmed_calories or 0,
                    )
                    for meal in meals
                ],
                consumed_calories=summary.consumed_calories,
                daily_goal=current_user.daily_calorie_goal,
                remaining_calories=summary.remaining_calories,
                protein_g=macros["protein"] if known_macros else None,
                carbs_g=macros["carbohydrates"] if known_macros else None,
                fat_g=macros["fat"] if known_macros else None,
                target_protein_g=target_protein,
                target_carbs_g=target_carbs,
                target_fat_g=target_fat,
            ),
            user_context=user_context,
            locale=lang,
            user_id=current_user.id,
        )
        headline = ai_result.headline or fallback_headline
        standout = ai_result.standout or fallback_standout
        balance_note = ai_result.balance_note or fallback_balance
        tip = ai_result.tomorrow_tip if ai_result.tomorrow_tip is not None else fallback_tip
    except Exception as e:
        logger.warning("AI day review failed, using deterministic fallback: %s", e)

    return DayReviewResponse(
        date=today.isoformat(),
        fingerprint=fingerprint,
        meal_count=len(meals),
        consumed_calories=summary.consumed_calories,
        daily_goal=current_user.daily_calorie_goal,
        remaining_calories=summary.remaining_calories,
        protein_g=macros["protein"] if known_macros else None,
        carbs_g=macros["carbohydrates"] if known_macros else None,
        fat_g=macros["fat"] if known_macros else None,
        headline=headline,
        standout=standout,
        balance_note=balance_note,
        tomorrow_tip=tip,
        coverage_note=coverage_note,
        meals=[
            DayReviewMeal(
                name=meal.meal_name or category_labels.get(meal.meal_category, meal.meal_category),
                category=meal.meal_category,
                calories=meal.confirmed_calories or 0,
            )
            for meal in meals
        ],
    )


@router.get("/review/{category}", response_model=MealOccasionReviewResponse)
async def review_meal_occasion(
    category: str,
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> MealOccasionReviewResponse:
    if category not in {"breakfast", "lunch", "dinner", "snack"}:
        raise HTTPException(status_code=422, detail="Unsupported meal category.")
    today = client_today()
    meal_repo = MealRepository(db)
    meals = [
        meal
        for meal in await meal_repo.get_user_meals_on_date(current_user.id, today)
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
    macros, known_macros, dominant = _meal_macros(meals)
    lang = _language(accept_language)
    copy = _REVIEW_COPY[lang]
    label = _CATEGORY_LABELS[lang][category]
    labels = _EVIDENCE_LABELS[lang]
    localized_macro = labels.get(dominant or "", "")

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
    coverage_note = copy["coverage"].format(category=label)

    # Deterministic fallback copy (used if the AI call fails).
    opening = copy["one"] if len(meals) == 1 else copy["many"]
    fallback_headline = opening.format(category=label, count=len(meals))
    fallback_balance = copy["macro"].format(macro=localized_macro) if known_macros else copy["mixed"]
    fallback_suggestion = (
        copy["suggestion"] if len({item.name.lower() for meal in meals for item in meal.items}) < 4 else None
    )
    fallback_tags = [_MACRO_TAGS[dominant]] if dominant else []

    headline, balance_note, suggestion, tags = fallback_headline, fallback_balance, fallback_suggestion, fallback_tags
    try:
        summary_service = SummaryService(db)
        summary = await summary_service.sync_daily_summary(current_user.id, today)
        user_context = await _build_user_context(db, current_user, accept_language)
        target_protein, target_carbs, target_fat = _macro_targets(current_user)
        ai_service = AICalorieEstimationService(db)
        ai_result = await ai_service.review_meal_occasion(
            MealOccasionReviewRequest(
                category=category,
                meal_names=[meal.meal_name for meal in meals if meal.meal_name],
                total_calories=total_calories,
                protein_g=macros["protein"] if known_macros else None,
                carbs_g=macros["carbohydrates"] if known_macros else None,
                fat_g=macros["fat"] if known_macros else None,
                target_protein_g=target_protein,
                target_carbs_g=target_carbs,
                target_fat_g=target_fat,
                remaining_calories=summary.remaining_calories,
                daily_goal=current_user.daily_calorie_goal,
            ),
            user_context=user_context,
            locale=lang,
            user_id=current_user.id,
        )
        headline = ai_result.headline or fallback_headline
        balance_note = ai_result.balance_note or fallback_balance
        suggestion = ai_result.suggestion if ai_result.suggestion is not None else fallback_suggestion
        tags = ai_result.tags or fallback_tags
    except Exception as e:
        logger.warning("AI meal occasion review failed, using deterministic fallback: %s", e)

    return MealOccasionReviewResponse(
        category=category,
        meal_ids=meal_ids,
        fingerprint=fingerprint,
        meal_count=len(meals),
        total_calories=total_calories,
        protein_g=macros["protein"] if known_macros else None,
        carbs_g=macros["carbohydrates"] if known_macros else None,
        fat_g=macros["fat"] if known_macros else None,
        headline=headline,
        balance_note=balance_note,
        suggestion=suggestion,
        tags=tags,
        coverage_note=coverage_note,
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
    "de": {
        "summary": "Du hast {consumed} kcal von deinem heutigen Richtwert von {goal} kcal erfasst. Dir bleiben {remaining} kcal.",
        "note": "Im Moment ist kein weiterer Mahlzeitenvorschlag nötig.",
    },
    "fr": {
        "summary": "Tu as enregistré {consumed} kcal sur la référence de {goal} kcal d’aujourd’hui. Il te reste {remaining} kcal.",
        "note": "Aucune autre suggestion de repas n’est nécessaire pour le moment.",
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
    today = client_today()
    preferences = preferences or MealCompletionPreferences()

    # Extract primary language code
    lang = "en"
    if accept_language:
        primary = accept_language.split(",")[0].split("-")[0].strip().lower()
        if primary in ["en", "it", "es", "zh", "ja", "ar", "de", "fr"]:
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
