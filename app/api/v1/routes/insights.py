import datetime as dt
import logging
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.services.correction_context_service import AICorrectionContextService
from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.daily_summary import DailySummary
from app.models.meal import Meal
from app.models.user import User
from app.schemas.insights import PatternInsightsResponse, WeeklyReportResponse

logger = logging.getLogger("app.api.insights")
router = APIRouter()


@router.get("/summary/weekly", response_model=WeeklyReportResponse)
async def get_weekly_report(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WeeklyReportResponse:
    """Computes weekly caloric stats and generates a real AI observation."""
    if not current_user.is_premium:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Calry Premium subscription required to view weekly reports.",
        )

    today = dt.date.today()
    start_date = today - dt.timedelta(days=6)

    stmt = (
        select(DailySummary)
        .where(
            DailySummary.user_id == current_user.id,
            DailySummary.date >= start_date,
            DailySummary.date <= today,
        )
    )
    result = await db.execute(stmt)
    summaries = result.scalars().all()

    if not summaries:
        return WeeklyReportResponse(
            average_calories=0,
            days_within_target=0,
            highest_calories=0,
            lowest_calories=0,
            most_frequent_meal="None",
            ai_observation="Log a few meals to see your first AI observation.",
        )

    total_calories = 0
    days_within_target = 0
    highest_cal = 0
    lowest_cal = 99999

    for s in summaries:
        total_calories += s.consumed_calories
        goal = s.consumed_calories + s.remaining_calories - s.burned_calories
        if goal > 0 and 0.90 <= s.consumed_calories / goal <= 1.05:
            days_within_target += 1
        if s.consumed_calories > highest_cal:
            highest_cal = s.consumed_calories
        if s.consumed_calories < lowest_cal:
            lowest_cal = s.consumed_calories

    if lowest_cal == 99999:
        lowest_cal = 0

    avg_calories = round(total_calories / len(summaries))

    start_dt = dt.datetime.combine(start_date, dt.time.min).replace(tzinfo=dt.UTC)
    meal_stmt = select(Meal).where(
        Meal.user_id == current_user.id,
        Meal.created_at >= start_dt,
    )
    meal_result = await db.execute(meal_stmt)
    meals = meal_result.scalars().all()

    most_common_meal = None
    if meals:
        meal_names = [m.meal_name for m in meals if m.meal_name]
        if meal_names:
            most_common_meal = Counter(meal_names).most_common(1)[0][0]

    goal_kcal = current_user.daily_calorie_goal
    provider = OpenRouterProvider()
    ai_observation = await provider.generate_weekly_observation(
        avg_calories=avg_calories,
        days_in_target=days_within_target,
        total_days=len(summaries),
        highest=highest_cal,
        lowest=lowest_cal,
        most_frequent_meal=most_common_meal,
        goal=goal_kcal,
    )

    return WeeklyReportResponse(
        average_calories=avg_calories,
        days_within_target=days_within_target,
        highest_calories=highest_cal,
        lowest_calories=lowest_cal,
        most_frequent_meal=most_common_meal or "None",
        ai_observation=ai_observation,
    )


@router.get("/insights/patterns", response_model=PatternInsightsResponse)
async def get_pattern_insights(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PatternInsightsResponse:
    """Returns real AI-generated pattern insights from the user's tracking history."""
    if not current_user.is_premium:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Calry Premium subscription required to view AI pattern insights.",
        )

    today = dt.date.today()
    start_date = today - dt.timedelta(days=29)

    stmt = select(DailySummary).where(
        DailySummary.user_id == current_user.id,
        DailySummary.date >= start_date,
    )
    result = await db.execute(stmt)
    summaries = result.scalars().all()

    days_logged = len([s for s in summaries if s.consumed_calories > 0])
    days_in_target = 0
    total_cals = 0
    goal_kcal = current_user.daily_calorie_goal

    for s in summaries:
        total_cals += s.consumed_calories
        goal = s.consumed_calories + s.remaining_calories - s.burned_calories
        if goal > 0 and 0.90 <= s.consumed_calories / goal <= 1.05:
            days_in_target += 1

    avg_cals = round(total_cals / days_logged) if days_logged > 0 else 0

    correction_service = AICorrectionContextService(db)
    correction_summary = await correction_service.get_user_correction_summary(current_user.id)
    avg_correction_pct = await correction_service.get_average_correction_percent(current_user.id)

    provider = OpenRouterProvider()
    patterns = await provider.generate_pattern_insights(
        correction_summary=correction_summary,
        avg_correction_pct=avg_correction_pct,
        days_logged=days_logged,
        days_in_target=days_in_target,
        avg_calories=avg_cals,
        goal=goal_kcal,
    )

    return PatternInsightsResponse(patterns=patterns)
