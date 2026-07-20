import datetime as dt
import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DayFeatures:
    date: dt.date
    calories: int
    goal_calories: int
    burned_calories: int
    water_glasses: int
    meal_count: int
    meal_categories: dict[str, int]
    protein_g: float
    carbs_g: float
    fat_g: float
    macro_meal_count: int

    @property
    def logged(self) -> bool:
        return self.calories > 0 or self.meal_count > 0

    @property
    def within_goal(self) -> bool:
        return self.goal_calories > 0 and 0.90 <= self.calories / self.goal_calories <= 1.05


@dataclass(frozen=True)
class CorrectionFeatures:
    date: dt.date
    source_type: str
    meal_category: str
    correction_percent: float


@dataclass(frozen=True)
class FeatureSnapshot:
    period_days: int
    start_date: dt.date
    end_date: dt.date
    days: tuple[DayFeatures, ...]
    corrections: tuple[CorrectionFeatures, ...]
    current_weight_kg: float | None = None
    protein_goal_g: int | None = None
    carbs_goal_g: int | None = None
    fat_goal_g: int | None = None

    @property
    def logged_days(self) -> tuple[DayFeatures, ...]:
        return tuple(day for day in self.days if day.logged)

    @property
    def days_logged(self) -> int:
        return len(self.logged_days)

    @property
    def average_calories(self) -> int:
        values = [day.calories for day in self.logged_days]
        return round(sum(values) / len(values)) if values else 0

    @property
    def calorie_variance(self) -> float:
        values = [day.calories for day in self.logged_days]
        return round(statistics.pvariance(values), 2) if len(values) >= 2 else 0.0

    @property
    def longest_logging_streak(self) -> int:
        longest = current = 0
        for day in self.days:
            current = current + 1 if day.logged else 0
            longest = max(longest, current)
        return longest

    @property
    def total_meals(self) -> int:
        return sum(day.meal_count for day in self.days)


class FeatureExtractor:
    """Converts persistence models into deterministic, LLM-free features."""

    @staticmethod
    def _date(value: dt.datetime) -> dt.date:
        return value.date()

    @classmethod
    def extract(
        cls,
        *,
        period_days: int,
        end_date: dt.date,
        calorie_goal: int,
        summaries: Iterable[Any],
        meals: Iterable[Any],
        current_weight_kg: float | None = None,
        protein_goal_g: int | None = None,
        carbs_goal_g: int | None = None,
        fat_goal_g: int | None = None,
    ) -> FeatureSnapshot:
        start_date = end_date - dt.timedelta(days=period_days - 1)
        summary_by_date = {summary.date: summary for summary in summaries}
        meals_by_date: dict[dt.date, list[Any]] = {}
        corrections: list[CorrectionFeatures] = []

        for meal in meals:
            meal_date = cls._date(meal.created_at)
            if not start_date <= meal_date <= end_date:
                continue
            meals_by_date.setdefault(meal_date, []).append(meal)
            correction_percent = getattr(meal, "correction_percent", None)
            if getattr(meal, "confirmed_calories", None) is not None and correction_percent is not None:
                corrections.append(
                    CorrectionFeatures(
                        date=meal_date,
                        source_type=getattr(meal, "source_type", "unknown") or "unknown",
                        meal_category=getattr(meal, "meal_category", "unknown") or "unknown",
                        correction_percent=float(correction_percent),
                    )
                )

        days: list[DayFeatures] = []
        for offset in range(period_days):
            date = start_date + dt.timedelta(days=offset)
            summary = summary_by_date.get(date)
            day_meals = meals_by_date.get(date, [])
            category_counts = Counter(
                category
                for meal in day_meals
                if (category := getattr(meal, "meal_category", None))
            )
            meal_calories = sum(max(0, int(getattr(meal, "estimated_calories", 0) or 0)) for meal in day_meals)
            calories = max(0, int(getattr(summary, "consumed_calories", meal_calories) or meal_calories))
            burned = max(0, int(getattr(summary, "burned_calories", 0) or 0))
            remaining = int(getattr(summary, "remaining_calories", 0) or 0)
            historical_goal = calories + remaining - burned if summary is not None else calorie_goal
            goal = historical_goal if historical_goal > 0 else calorie_goal
            macro_meals = [
                meal
                for meal in day_meals
                if any(
                    getattr(meal, field_name, None) is not None
                    for field_name in ("total_protein_g", "total_carbs_g", "total_fat_g")
                )
            ]
            days.append(
                DayFeatures(
                    date=date,
                    calories=calories,
                    goal_calories=max(0, int(goal)),
                    burned_calories=burned,
                    water_glasses=max(0, int(getattr(summary, "water_glasses", 0) or 0)),
                    meal_count=len(day_meals),
                    meal_categories=dict(category_counts),
                    protein_g=round(sum(float(getattr(meal, "total_protein_g", 0) or 0) for meal in day_meals), 2),
                    carbs_g=round(sum(float(getattr(meal, "total_carbs_g", 0) or 0) for meal in day_meals), 2),
                    fat_g=round(sum(float(getattr(meal, "total_fat_g", 0) or 0) for meal in day_meals), 2),
                    macro_meal_count=len(macro_meals),
                )
            )

        return FeatureSnapshot(
            period_days=period_days,
            start_date=start_date,
            end_date=end_date,
            days=tuple(days),
            corrections=tuple(sorted(corrections, key=lambda item: item.date)),
            current_weight_kg=current_weight_kg,
            protein_goal_g=protein_goal_g,
            carbs_goal_g=carbs_goal_g,
            fat_goal_g=fat_goal_g,
        )
