"""Weight history, trend smoothing and the adaptive calorie target.

The calorie target starts from a formula (Mifflin-St Jeor x activity), which is
a population average and can be off by several hundred kcal for one person.
Once the user has weighed in regularly while logging meals faithfully, the
energy balance reveals their real maintenance:

    maintenance = average net intake - daily weight change x 7700 kcal/kg

The suggestion is only made when the logged days are trustworthy: most days in
the window logged, and most logged days on target for calories and, when set,
macros. Then a weight change that differs from the planned one points at the
estimate, not at the user's behaviour. The suggestion is never applied
silently: the user reviews it and decides.
"""

import datetime as dt
import math
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.insights.features import DayFeatures, FeatureExtractor
from app.models.daily_summary import DailySummary
from app.models.meal import Meal
from app.models.user import User
from app.models.weight_entry import WeightEntry
from app.services.calorie_target_service import CalorieTargetService

KCAL_PER_KG = 7700.0
# Exponential smoothing per day (Hacker's Diet). Damps water-weight noise.
TREND_ALPHA = 0.1

WINDOW_DAYS = 28
MIN_WEIGH_INS = 4
MIN_SPAN_DAYS = 14
# Share of window days with a complete log (partial days do not count).
MIN_LOGGED_RATIO = 0.75
# A logged day below this share of its target is treated as incomplete.
COMPLETE_DAY_MIN_RATIO = 0.4
# Share of logged days on target.
MIN_ADHERENCE_RATIO = 0.7
CALORIE_BAND = (0.85, 1.10)
MACRO_BAND = (0.75, 1.25)
MIN_SUGGESTED_CHANGE = 100
MAX_STEP = 300
COOLDOWN_DAYS = 14
PLAUSIBLE_MAINTENANCE = (1000, 6000)

AdaptiveStatus = Literal[
    "not_enough_weights",
    "not_enough_logging",
    "low_adherence",
    "implausible",
    "recently_reviewed",
    "on_track",
    "adjust",
]


@dataclass(frozen=True)
class WeightPoint:
    date: dt.date
    weight_kg: float


@dataclass(frozen=True)
class AdaptiveTarget:
    status: AdaptiveStatus
    window_days: int
    weigh_ins: int
    weigh_in_span_days: int
    logged_days: int
    adherent_days: int
    current_goal: int
    required_weigh_ins: int = MIN_WEIGH_INS
    required_span_days: int = MIN_SPAN_DAYS
    required_logged_days: int = math.ceil(WINDOW_DAYS * MIN_LOGGED_RATIO)
    average_net_intake: int | None = None
    observed_maintenance: int | None = None
    expected_weekly_change_kg: float | None = None
    actual_weekly_change_kg: float | None = None
    recommended_goal: int | None = None
    confidence: float | None = None
    capped_at_minimum: bool = False

    @property
    def change(self) -> int | None:
        return None if self.recommended_goal is None else self.recommended_goal - self.current_goal


def smoothed_trend(points: list[WeightPoint]) -> list[float]:
    """Gap-aware exponential moving average, one value per point (sorted by date)."""
    trend: list[float] = []
    previous: WeightPoint | None = None
    for point in points:
        if previous is None:
            value = point.weight_kg
        else:
            gap = max(1, (point.date - previous.date).days)
            alpha = 1 - (1 - TREND_ALPHA) ** gap
            value = trend[-1] + alpha * (point.weight_kg - trend[-1])
        trend.append(round(value, 2))
        previous = point
    return trend


def slope_kg_per_day(points: list[WeightPoint]) -> float | None:
    """Least-squares slope of weight over time; None without two distinct days."""
    if len(points) < 2:
        return None
    origin = points[0].date
    xs = [(p.date - origin).days for p in points]
    ys = [p.weight_kg for p in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator


def _round_50(value: float) -> int:
    return int(math.floor(value / 50.0 + 0.5) * 50)


def _pace_adjustment(goal_type: str, target_pace: str | None) -> int:
    paces = CalorieTargetService.PACE_ADJUSTMENTS.get(goal_type, {"balanced": 0})
    return paces.get(target_pace or "balanced", paces.get("balanced", 0))


def _complete(day: DayFeatures) -> bool:
    return day.logged and day.goal_calories > 0 and day.calories >= day.goal_calories * COMPLETE_DAY_MIN_RATIO


def _within(value: float, goal: int | None, band: tuple[float, float]) -> bool:
    if not goal:
        return True
    return band[0] <= value / goal <= band[1]


def day_is_adherent(
    day: DayFeatures,
    *,
    protein_goal: int | None,
    carbs_goal: int | None,
    fat_goal: int | None,
) -> bool:
    # Logged exercise raises the day's allowance, as on the dashboard.
    allowance = day.goal_calories + day.burned_calories
    if not _within(day.calories, allowance, CALORIE_BAND):
        return False
    if day.macro_meal_count == 0:
        return True
    return (
        _within(day.protein_g, protein_goal, MACRO_BAND)
        and _within(day.carbs_g, carbs_goal, MACRO_BAND)
        and _within(day.fat_g, fat_goal, MACRO_BAND)
    )


def evaluate_adaptive_target(
    *,
    days: list[DayFeatures],
    weights: list[WeightPoint],
    current_goal: int,
    goal_type: str,
    target_pace: str | None,
    sex: str | None,
    protein_goal: int | None = None,
    carbs_goal: int | None = None,
    fat_goal: int | None = None,
    reviewed_at: dt.datetime | None = None,
    now: dt.datetime | None = None,
) -> AdaptiveTarget:
    """Pure decision: ``days`` are the window's diary days (today excluded),
    ``weights`` the weigh-ins inside the window, sorted by date."""
    window = len(days)
    span = (weights[-1].date - weights[0].date).days if weights else 0
    complete = [day for day in days if _complete(day)]
    adherent = [
        day
        for day in complete
        if day_is_adherent(day, protein_goal=protein_goal, carbs_goal=carbs_goal, fat_goal=fat_goal)
    ]
    base = {
        "window_days": window,
        "weigh_ins": len(weights),
        "weigh_in_span_days": span,
        "logged_days": len(complete),
        "adherent_days": len(adherent),
        "current_goal": current_goal,
        "required_logged_days": math.ceil(window * MIN_LOGGED_RATIO),
    }

    # Right after an apply or dismiss the window still holds days logged
    # against the previous target, so neither "on track" nor "low adherence"
    # would be true yet: say only that it was reviewed.
    now = now or dt.datetime.now(dt.UTC)
    if reviewed_at is not None and reviewed_at.tzinfo is None:
        reviewed_at = reviewed_at.replace(tzinfo=dt.UTC)
    if reviewed_at is not None and now - reviewed_at < dt.timedelta(days=COOLDOWN_DAYS):
        return AdaptiveTarget(status="recently_reviewed", **base)

    if len(weights) < MIN_WEIGH_INS or span < MIN_SPAN_DAYS:
        return AdaptiveTarget(status="not_enough_weights", **base)
    if len(complete) < math.ceil(window * MIN_LOGGED_RATIO):
        return AdaptiveTarget(status="not_enough_logging", **base)
    if len(adherent) < math.ceil(len(complete) * MIN_ADHERENCE_RATIO):
        return AdaptiveTarget(status="low_adherence", **base)

    slope = slope_kg_per_day(weights) or 0.0
    net_intake = sum(day.calories - day.burned_calories for day in complete) / len(complete)
    observed = net_intake - slope * KCAL_PER_KG
    pace = _pace_adjustment(goal_type, target_pace)
    implied_maintenance = current_goal - pace
    measures = {
        "average_net_intake": round(net_intake),
        "observed_maintenance": round(observed),
        "expected_weekly_change_kg": round((net_intake - implied_maintenance) * 7 / KCAL_PER_KG, 2),
        "actual_weekly_change_kg": round(slope * 7, 2),
    }
    if not PLAUSIBLE_MAINTENANCE[0] <= observed <= PLAUSIBLE_MAINTENANCE[1]:
        return AdaptiveTarget(status="implausible", **base, **measures)

    # More weigh-ins over a longer span earn the observed value more trust;
    # the rest stays with the target the user already has.
    confidence = (
        0.5
        + 0.25 * min(1.0, (span - MIN_SPAN_DAYS) / MIN_SPAN_DAYS)
        + 0.25 * min(1.0, (len(weights) - MIN_WEIGH_INS) / 8)
    )
    delta = confidence * ((observed + pace) - current_goal)
    delta = max(-MAX_STEP, min(MAX_STEP, delta))
    recommended = _round_50(current_goal + delta)
    floor = 1200 if sex == "female" else 1500
    capped = False
    if recommended < floor:
        # Never suggest going below the product floor; a target the user
        # already set lower stays theirs.
        recommended, capped = min(current_goal, floor), True
    measures["confidence"] = round(confidence, 2)

    if abs(recommended - current_goal) < MIN_SUGGESTED_CHANGE:
        return AdaptiveTarget(status="on_track", **base, **measures)
    return AdaptiveTarget(
        status="adjust",
        **base,
        **measures,
        recommended_goal=recommended,
        capped_at_minimum=capped,
    )


class WeightService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def entries(self, user_id: int, since: dt.date | None = None) -> list[WeightEntry]:
        stmt = select(WeightEntry).where(WeightEntry.user_id == user_id)
        if since is not None:
            stmt = stmt.where(WeightEntry.local_date >= since)
        result = await self.db.execute(stmt.order_by(WeightEntry.local_date))
        return list(result.scalars().all())

    async def entry_on(self, user_id: int, day: dt.date) -> WeightEntry | None:
        result = await self.db.execute(
            select(WeightEntry).where(WeightEntry.user_id == user_id, WeightEntry.local_date == day)
        )
        return result.scalar_one_or_none()

    async def upsert(self, user: User, day: dt.date, weight_kg: float, source: str = "manual") -> WeightEntry:
        """One value per day: a new weigh-in replaces the day's earlier one."""
        weight_kg = round(weight_kg, 2)
        entry = await self.entry_on(user.id, day)
        if entry is None:
            entry = WeightEntry(user_id=user.id, local_date=day, weight_kg=weight_kg, source=source)
            self.db.add(entry)
        else:
            entry.weight_kg = weight_kg
            entry.source = source
        await self.db.flush()
        await self.sync_current_weight(user)
        return entry

    async def sync_current_weight(self, user: User) -> None:
        """``users.weight_kg`` follows the most recent weigh-in."""
        result = await self.db.execute(
            select(WeightEntry.weight_kg)
            .where(WeightEntry.user_id == user.id)
            .order_by(desc(WeightEntry.local_date))
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        if latest is not None and latest != user.weight_kg:
            user.weight_kg = latest
            await self.db.flush()

    async def adaptive_target(self, user: User, today: dt.date) -> AdaptiveTarget:
        start = today - dt.timedelta(days=WINDOW_DAYS)
        end = today - dt.timedelta(days=1)  # today is still being logged
        summaries = await self.db.execute(
            select(DailySummary).where(
                DailySummary.user_id == user.id,
                DailySummary.date >= start,
                DailySummary.date <= end,
            )
        )
        meals = await self.db.execute(
            select(Meal).where(Meal.user_id == user.id, Meal.local_date >= start, Meal.local_date <= end)
        )
        snapshot = FeatureExtractor.extract(
            period_days=WINDOW_DAYS,
            end_date=end,
            calorie_goal=user.daily_calorie_goal,
            summaries=list(summaries.scalars().all()),
            meals=list(meals.scalars().all()),
        )
        weights = [
            WeightPoint(entry.local_date, entry.weight_kg)
            for entry in await self.entries(user.id, since=start)
            if entry.local_date <= today
        ]
        return evaluate_adaptive_target(
            days=list(snapshot.days),
            weights=weights,
            current_goal=user.daily_calorie_goal,
            goal_type=user.goal_type,
            target_pace=user.target_pace,
            sex=user.sex,
            protein_goal=user.daily_protein_goal,
            carbs_goal=user.daily_carbs_goal,
            fat_goal=user.daily_fat_goal,
            reviewed_at=user.adaptive_target_reviewed_at,
        )
