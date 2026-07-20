import datetime as dt
import logging
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.ai.providers.openrouter import OpenRouterProvider
from app.dependencies.db import get_db
from app.dependencies.premium import require_premium_user
from app.insights import FeatureExtractor, FeatureSnapshot, InsightEngine
from app.models.daily_summary import DailySummary
from app.models.meal import Meal
from app.models.user import User
from app.schemas.insights import PatternInsightsResponse, WeeklyReportResponse

logger = logging.getLogger("app.api.insights")
router = APIRouter()


async def _load_features(
    db: AsyncSession,
    user: User,
    *,
    period_days: int,
    end_date: dt.date,
) -> tuple[FeatureSnapshot, list[Meal]]:
    start_date = end_date - dt.timedelta(days=period_days - 1)
    start_datetime = dt.datetime.combine(start_date, dt.time.min, tzinfo=dt.UTC)
    end_datetime = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)

    summary_result = await db.execute(
        select(DailySummary).where(
            DailySummary.user_id == user.id,
            DailySummary.date >= start_date,
            DailySummary.date <= end_date,
        )
    )
    meal_result = await db.execute(
        select(Meal).where(
            Meal.user_id == user.id,
            Meal.created_at >= start_datetime,
            Meal.created_at < end_datetime,
        )
    )
    summaries = list(summary_result.scalars().all())
    meals = list(meal_result.scalars().all())
    snapshot = FeatureExtractor.extract(
        period_days=period_days,
        end_date=end_date,
        calorie_goal=user.daily_calorie_goal,
        summaries=summaries,
        meals=meals,
        current_weight_kg=user.weight_kg,
    )
    return snapshot, meals


@router.get("/summary/weekly", response_model=WeeklyReportResponse)
async def get_weekly_report(
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: Annotated[str | None, Header()] = None,
) -> WeeklyReportResponse:
    """Returns deterministic weekly metrics plus one voiced verified pattern."""
    snapshot, meals = await _load_features(db, current_user, period_days=7, end_date=dt.date.today())
    logged_days = snapshot.logged_days

    meal_names = [meal.meal_name for meal in meals if meal.meal_name]
    most_common_meal = Counter(meal_names).most_common(1)[0][0] if meal_names else None
    calories = [day.calories for day in logged_days]
    days_within_target = sum(day.within_goal for day in logged_days)

    patterns = InsightEngine().generate(snapshot, limit=1)
    observation = await OpenRouterProvider().generate_weekly_observation(
        patterns[0] if patterns else None,
        days_analyzed=7,
        locale=accept_language or "en",
    )

    return WeeklyReportResponse(
        average_calories=snapshot.average_calories,
        days_within_target=days_within_target,
        highest_calories=max(calories, default=0),
        lowest_calories=min(calories, default=0),
        most_frequent_meal=most_common_meal,
        days_logged=snapshot.days_logged,
        ai_observation=observation,
    )


@router.get("/insights/patterns", response_model=PatternInsightsResponse)
async def get_pattern_insights(
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: Annotated[str | None, Header()] = None,
) -> PatternInsightsResponse:
    """Voices at most four patterns already verified by deterministic detectors."""
    snapshot, _ = await _load_features(db, current_user, period_days=30, end_date=dt.date.today())
    verified_patterns = InsightEngine().generate(snapshot, limit=4)
    insights = await OpenRouterProvider().generate_pattern_insights(
        verified_patterns,
        locale=accept_language or "en",
    )
    return PatternInsightsResponse(
        patterns=insights,
        days_logged=snapshot.days_logged,
        period_days=snapshot.period_days,
    )
