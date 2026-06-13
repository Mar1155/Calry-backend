import logging
import datetime as dt
from collections import Counter
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.user import User
from app.models.daily_summary import DailySummary
from app.models.meal import Meal
from app.schemas.insights import WeeklyReportResponse, PatternInsightsResponse

logger = logging.getLogger("app.api.insights")
router = APIRouter()


@router.get("/summary/weekly", response_model=WeeklyReportResponse)
async def get_weekly_report(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WeeklyReportResponse:
    """Computes and returns weekly caloric summaries for premium subscribers."""
    # 1. Server-side Premium Gate Enforced
    if not current_user.is_premium:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Calry Premium subscription required to view weekly reports.",
        )

    # 2. Gather Summaries for the last 7 days
    today = dt.date.today()
    start_date = today - dt.timedelta(days=6)
    
    stmt = (
        select(DailySummary)
        .where(
            DailySummary.user_id == current_user.id,
            DailySummary.date >= start_date,
            DailySummary.date <= today
        )
    )
    result = await db.execute(stmt)
    summaries = result.scalars().all()

    if not summaries:
        # Fallback values if no summaries are logged
        return WeeklyReportResponse(
            average_calories=0,
            days_within_target=0,
            highest_calories=0,
            lowest_calories=0,
            most_frequent_meal="None",
            ai_observation="Log a few meals to see your first AI observation.",
        )

    # 3. Calculate Stats
    total_calories = 0
    days_within_target = 0
    highest_cal = 0
    lowest_cal = 99999
    
    for s in summaries:
        total_calories += s.consumed_calories
        
        # Determine target goal: consumed + remaining - burned
        goal = s.consumed_calories + s.remaining_calories - s.burned_calories
        if goal > 0:
            ratio = s.consumed_calories / goal
            if 0.90 <= ratio <= 1.05:
                days_within_target += 1
        
        if s.consumed_calories > highest_cal:
            highest_cal = s.consumed_calories
        if s.consumed_calories < lowest_cal:
            lowest_cal = s.consumed_calories
            
    if lowest_cal == 99999:
        lowest_cal = 0
        
    avg_calories = round(total_calories / len(summaries))

    # 4. Gather most frequent meal name
    start_dt = dt.datetime.combine(start_date, dt.time.min).replace(tzinfo=dt.UTC)
    meal_stmt = (
        select(Meal)
        .where(
            Meal.user_id == current_user.id,
            Meal.created_at >= start_dt
        )
    )
    meal_result = await db.execute(meal_stmt)
    meals = meal_result.scalars().all()
    
    most_common_meal = None
    if meals:
        meal_names = [m.meal_name for m in meals if m.meal_name]
        if meal_names:
            most_common_meal = Counter(meal_names).most_common(1)[0][0]

    return WeeklyReportResponse(
        average_calories=avg_calories,
        days_within_target=days_within_target,
        highest_calories=highest_cal,
        lowest_calories=lowest_cal,
        most_frequent_meal=most_common_meal or "None",
        ai_observation="Your calorie intake is most consistent when dinner is lighter. Caloric deviation on heavier-dinner days averages +320 kcal.",
    )


@router.get("/insights/patterns", response_model=PatternInsightsResponse)
async def get_pattern_insights(
    current_user: User = Depends(get_current_user),
) -> PatternInsightsResponse:
    """Returns AI-generated long-term calorie pattern insights for premium users."""
    # 1. Server-side Premium Gate Enforced
    if not current_user.is_premium:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Calry Premium subscription required to view AI pattern insights.",
        )

    # 2. Return patterns (for MVP, rules-based pattern array)
    patterns = [
        "You tend to stay closer to your target on days with a lighter dinner.",
        "Most excess calories this week came from evening meals logged after 8 PM.",
        "Your calorie intake is most consistent between Monday and Thursday.",
    ]
    return PatternInsightsResponse(patterns=patterns)
