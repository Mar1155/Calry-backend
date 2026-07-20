import datetime as dt
import logging
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.ai.providers.openrouter import OpenRouterProvider
from app.core.config import settings
from app.dependencies.db import get_db
from app.dependencies.premium import require_premium_user
from app.insights import FeatureExtractor, FeatureSnapshot, InsightEngine
from app.insights.snapshot_service import InsightSnapshotService
from app.models.daily_summary import DailySummary
from app.models.meal import Meal
from app.models.user import User
from app.schemas.insights import InsightStoriesResponse, PatternInsightsResponse, StoryEvidence, WeeklyReportResponse

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
        protein_goal_g=user.daily_protein_goal,
        carbs_goal_g=user.daily_carbs_goal,
        fat_goal_g=user.daily_fat_goal,
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


@router.get("/insights/stories", response_model=InsightStoriesResponse)
async def get_insight_stories(
    scope: str = Query(default="rolling_30d", pattern="^(rolling_30d|weekly_current)$"),
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: Annotated[str | None, Header()] = None,
) -> InsightStoriesResponse:
    if not settings.ENABLE_VERSIONED_INSIGHT_SNAPSHOTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Versioned insights are disabled.")
    return await InsightSnapshotService(db).get_stories(
        current_user,
        scope=scope,
        locale=accept_language or "en",
    )


@router.get("/insights/stories/{story_id}/evidence", response_model=list[StoryEvidence])
async def get_story_evidence(
    story_id: str,
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
) -> list[StoryEvidence]:
    if not settings.ENABLE_VERSIONED_INSIGHT_SNAPSHOTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Versioned insights are disabled.")
    evidence = await InsightSnapshotService(db).story_evidence(current_user.id, story_id)
    if evidence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insight story not found.")
    return [StoryEvidence.model_validate(item) for item in evidence]


@router.get("/reports/weekly/current", response_model=InsightStoriesResponse)
async def get_current_weekly_stories(
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: Annotated[str | None, Header()] = None,
) -> InsightStoriesResponse:
    if not settings.ENABLE_VERSIONED_INSIGHT_SNAPSHOTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Versioned insights are disabled.")
    return await InsightSnapshotService(db).get_stories(
        current_user,
        scope="weekly_current",
        locale=accept_language or "en",
    )


@router.get("/reports/weekly/{period_start}", response_model=InsightStoriesResponse)
async def get_closed_weekly_stories(
    period_start: dt.date,
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: Annotated[str | None, Header()] = None,
) -> InsightStoriesResponse:
    if not settings.ENABLE_VERSIONED_INSIGHT_SNAPSHOTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Versioned insights are disabled.")
    try:
        return await InsightSnapshotService(db).get_stories(
            current_user,
            scope="weekly_closed",
            locale=accept_language or "en",
            period_start=period_start,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
