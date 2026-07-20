import statistics
from abc import ABC, abstractmethod

from app.insights.features import DayFeatures, FeatureSnapshot
from app.insights.patterns import VerifiedPattern


def _confidence(sample_size: int, *, strength: float = 0.5, minimum: float = 0.55) -> float:
    sample_score = min(1.0, sample_size / 14)
    return round(min(0.99, minimum + sample_score * 0.30 + min(1.0, strength) * 0.12), 3)


def _split_logged_days(snapshot: FeatureSnapshot) -> tuple[list[DayFeatures], list[DayFeatures]]:
    midpoint = snapshot.start_date + (snapshot.end_date - snapshot.start_date) / 2
    earlier = [day for day in snapshot.logged_days if day.date <= midpoint]
    recent = [day for day in snapshot.logged_days if day.date > midpoint]
    return earlier, recent


class PatternDetector(ABC):
    registry: list[type["PatternDetector"]] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstractmethods__", None):
            PatternDetector.registry.append(cls)

    @abstractmethod
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]: ...


class GoalConsistencyDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        days = snapshot.logged_days
        if len(days) < 3:
            return []
        within = sum(day.within_goal for day in days)
        rate = within / len(days)
        deviations = [abs(day.calories - day.goal_calories) for day in days if day.goal_calories > 0]
        return [
            VerifiedPattern(
                id="goal_consistency",
                category="consistency",
                confidence=_confidence(len(days), strength=abs(rate - 0.5) * 2),
                priority=84,
                novelty=0.72,
                concept="goal_adherence",
                payload={
                    "days_logged": len(days),
                    "days_within_target": within,
                    "adherence_rate": round(rate, 3),
                    "average_calories": snapshot.average_calories,
                    "calorie_variance": snapshot.calorie_variance,
                    "average_absolute_goal_difference": round(sum(deviations) / len(deviations)) if deviations else 0,
                },
            )
        ]


class WeekendDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        weekend = [day.calories for day in snapshot.logged_days if day.date.weekday() >= 5]
        weekdays = [day.calories for day in snapshot.logged_days if day.date.weekday() < 5]
        if len(weekend) < 2 or len(weekdays) < 3:
            return []
        weekend_avg = sum(weekend) / len(weekend)
        weekday_avg = sum(weekdays) / len(weekdays)
        if weekday_avg <= 0:
            return []
        difference_pct = (weekend_avg - weekday_avg) / weekday_avg
        if abs(difference_pct) < 0.08:
            return []
        return [
            VerifiedPattern(
                id="weekend_difference",
                category="timing",
                confidence=_confidence(len(weekend) + len(weekdays), strength=abs(difference_pct)),
                priority=82,
                novelty=0.90,
                concept="weekend_calories",
                payload={
                    "weekend_days": len(weekend),
                    "weekday_days": len(weekdays),
                    "weekend_average_calories": round(weekend_avg),
                    "weekday_average_calories": round(weekday_avg),
                    "difference_calories": round(weekend_avg - weekday_avg),
                    "difference_percent": round(difference_pct, 3),
                },
            )
        ]


class MealDistributionDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        days = snapshot.logged_days
        if len(days) < 3 or snapshot.total_meals < 4:
            return []
        totals: dict[str, int] = {}
        skipped = dict.fromkeys(("breakfast", "lunch", "dinner"), 0)
        for day in days:
            for category, count in day.meal_categories.items():
                totals[category] = totals.get(category, 0) + count
            for category in skipped:
                if day.meal_categories.get(category, 0) == 0:
                    skipped[category] += 1
        most_common_category = max(totals, key=totals.get) if totals else "unknown"
        return [
            VerifiedPattern(
                id="meal_distribution",
                category="meal_distribution",
                confidence=_confidence(len(days), strength=min(1.0, snapshot.total_meals / (len(days) * 3))),
                priority=69,
                novelty=0.62,
                concept="meal_frequency",
                payload={
                    "days_logged": len(days),
                    "total_meals": snapshot.total_meals,
                    "average_meals_per_logged_day": round(snapshot.total_meals / len(days), 2),
                    "meal_category_counts": totals,
                    "most_logged_category": most_common_category,
                    "days_without_breakfast_log": skipped["breakfast"],
                    "days_without_lunch_log": skipped["lunch"],
                    "days_without_dinner_log": skipped["dinner"],
                },
            )
        ]


class MacroBalanceDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        days = [day for day in snapshot.logged_days if day.macro_meal_count > 0]
        macro_meals = sum(day.macro_meal_count for day in days)
        if len(days) < 3 or macro_meals < 5:
            return []
        protein = sum(day.protein_g for day in days) / len(days)
        carbs = sum(day.carbs_g for day in days) / len(days)
        fat = sum(day.fat_g for day in days) / len(days)
        macro_calories = protein * 4 + carbs * 4 + fat * 9
        if macro_calories <= 0:
            return []
        return [
            VerifiedPattern(
                id="macro_balance",
                category="macros",
                confidence=_confidence(macro_meals, strength=min(1.0, macro_meals / max(1, snapshot.total_meals))),
                priority=73,
                novelty=0.74,
                concept="macro_balance",
                payload={
                    "days_with_macro_data": len(days),
                    "meals_with_macro_data": macro_meals,
                    "average_protein_g": round(protein, 1),
                    "average_carbs_g": round(carbs, 1),
                    "average_fat_g": round(fat, 1),
                    "protein_calorie_share": round(protein * 4 / macro_calories, 3),
                    "carbs_calorie_share": round(carbs * 4 / macro_calories, 3),
                    "fat_calorie_share": round(fat * 9 / macro_calories, 3),
                },
            )
        ]


class WaterDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        days = [day for day in snapshot.days if day.water_glasses > 0]
        if len(days) < 4:
            return []
        values = [day.water_glasses for day in days]
        midpoint = len(days) // 2
        earlier = values[:midpoint]
        recent = values[midpoint:]
        return [
            VerifiedPattern(
                id="hydration_consistency",
                category="hydration",
                confidence=_confidence(len(days), strength=1 / (1 + statistics.pvariance(values))),
                priority=61,
                novelty=0.68,
                concept="hydration",
                payload={
                    "days_with_water_logs": len(days),
                    "average_glasses": round(sum(values) / len(values), 1),
                    "minimum_glasses": min(values),
                    "maximum_glasses": max(values),
                    "variance": round(statistics.pvariance(values), 2),
                    "earlier_average_glasses": round(sum(earlier) / len(earlier), 1),
                    "recent_average_glasses": round(sum(recent) / len(recent), 1),
                    "average_glasses_change": round(
                        sum(recent) / len(recent) - sum(earlier) / len(earlier),
                        1,
                    ),
                },
            )
        ]


class ActivityDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        observed = [day for day in snapshot.days if day.logged or day.burned_calories > 0]
        active = [day for day in observed if day.burned_calories > 0]
        if len(observed) < 5 or len(active) < 2:
            return []
        midpoint = len(observed) // 2
        earlier = observed[:midpoint]
        recent = observed[midpoint:]
        earlier_rate = sum(day.burned_calories > 0 for day in earlier) / len(earlier)
        recent_rate = sum(day.burned_calories > 0 for day in recent) / len(recent)
        return [
            VerifiedPattern(
                id="activity_frequency",
                category="activity",
                confidence=_confidence(len(observed), strength=len(active) / len(observed)),
                priority=64,
                novelty=0.70,
                concept="activity",
                payload={
                    "days_observed": len(observed),
                    "active_days": len(active),
                    "active_day_rate": round(len(active) / len(observed), 3),
                    "average_burned_calories_on_active_days": round(
                        sum(day.burned_calories for day in active) / len(active)
                    ),
                    "total_burned_calories": sum(day.burned_calories for day in active),
                    "earlier_active_day_rate": round(earlier_rate, 3),
                    "recent_active_day_rate": round(recent_rate, 3),
                    "active_day_rate_change": round(recent_rate - earlier_rate, 3),
                },
            )
        ]


class LoggingHabitDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        if snapshot.days_logged < 3:
            return []
        return [
            VerifiedPattern(
                id="logging_consistency",
                category="logging",
                confidence=_confidence(snapshot.days_logged, strength=snapshot.days_logged / snapshot.period_days),
                priority=78,
                novelty=0.55,
                concept="logging_consistency",
                payload={
                    "period_days": snapshot.period_days,
                    "days_logged": snapshot.days_logged,
                    "logging_rate": round(snapshot.days_logged / snapshot.period_days, 3),
                    "longest_streak": snapshot.longest_logging_streak,
                    "total_meals": snapshot.total_meals,
                },
            )
        ]


class AIAccuracyDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        corrections = list(snapshot.corrections)
        if len(corrections) < 5:
            return []
        absolute = [abs(item.correction_percent) for item in corrections]
        within_ten = sum(value <= 10 for value in absolute)
        source_counts: dict[str, int] = {}
        for item in corrections:
            source_counts[item.source_type] = source_counts.get(item.source_type, 0) + 1
        return [
            VerifiedPattern(
                id="ai_estimation_accuracy",
                category="ai_accuracy",
                confidence=_confidence(len(corrections), strength=within_ten / len(corrections)),
                priority=80,
                novelty=0.94,
                concept="ai_accuracy",
                payload={
                    "confirmed_meals": len(corrections),
                    "average_absolute_correction_percent": round(sum(absolute) / len(absolute), 1),
                    "median_absolute_correction_percent": round(statistics.median(absolute), 1),
                    "average_signed_correction_percent": round(
                        sum(item.correction_percent for item in corrections) / len(corrections),
                        1,
                    ),
                    "estimates_within_ten_percent": within_ten,
                    "accuracy_rate_within_ten_percent": round(within_ten / len(corrections), 3),
                    "correction_source_counts": source_counts,
                },
            )
        ]


class LearningProgressDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        corrections = list(snapshot.corrections)
        if len(corrections) < 6:
            return []
        midpoint = len(corrections) // 2
        older = [abs(item.correction_percent) for item in corrections[:midpoint]]
        recent = [abs(item.correction_percent) for item in corrections[midpoint:]]
        older_avg = sum(older) / len(older)
        recent_avg = sum(recent) / len(recent)
        if older_avg <= 0:
            return []
        change = (recent_avg - older_avg) / older_avg
        if abs(change) < 0.12:
            return []
        return [
            VerifiedPattern(
                id="learning_progress",
                category="learning",
                confidence=_confidence(len(corrections), strength=abs(change)),
                priority=86,
                novelty=0.98,
                concept="ai_learning",
                payload={
                    "older_confirmed_meals": len(older),
                    "recent_confirmed_meals": len(recent),
                    "older_average_absolute_correction_percent": round(older_avg, 1),
                    "recent_average_absolute_correction_percent": round(recent_avg, 1),
                    "relative_error_change": round(change, 3),
                },
            )
        ]


class WeightTrendDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        # User currently stores one weight value, not timestamped weight history.
        # A single measurement cannot verify a trend.
        return []


class CaloriesTrendDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        earlier, recent = _split_logged_days(snapshot)
        if len(earlier) < 3 or len(recent) < 3:
            return []
        earlier_avg = sum(day.calories for day in earlier) / len(earlier)
        recent_avg = sum(day.calories for day in recent) / len(recent)
        if earlier_avg <= 0:
            return []
        change = (recent_avg - earlier_avg) / earlier_avg
        if abs(change) < 0.07:
            return []
        return [
            VerifiedPattern(
                id="calories_trend",
                category="calories",
                confidence=_confidence(len(earlier) + len(recent), strength=abs(change)),
                priority=76,
                novelty=0.82,
                concept="calorie_trend",
                payload={
                    "earlier_days": len(earlier),
                    "recent_days": len(recent),
                    "earlier_average_calories": round(earlier_avg),
                    "recent_average_calories": round(recent_avg),
                    "change_calories": round(recent_avg - earlier_avg),
                    "change_percent": round(change, 3),
                },
            )
        ]


class ImprovementDetector(PatternDetector):
    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        earlier, recent = _split_logged_days(snapshot)
        if len(earlier) < 3 or len(recent) < 3:
            return []
        earlier_rate = sum(day.within_goal for day in earlier) / len(earlier)
        recent_rate = sum(day.within_goal for day in recent) / len(recent)
        change = recent_rate - earlier_rate
        if abs(change) < 0.15:
            return []
        return [
            VerifiedPattern(
                id="goal_adherence_change",
                category="improvement",
                confidence=_confidence(len(earlier) + len(recent), strength=abs(change)),
                priority=88,
                novelty=0.96,
                concept="goal_adherence",
                payload={
                    "earlier_days": len(earlier),
                    "recent_days": len(recent),
                    "earlier_adherence_rate": round(earlier_rate, 3),
                    "recent_adherence_rate": round(recent_rate, 3),
                    "adherence_rate_change": round(change, 3),
                },
            )
        ]
