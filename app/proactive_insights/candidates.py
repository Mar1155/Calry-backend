import datetime as dt
import json
from collections import Counter, defaultdict
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.insights.patterns import VerifiedPattern


class ProactiveTrigger(StrEnum):
    MEAL_CREATED = "MealCreated"
    MEAL_UPDATED = "MealUpdated"
    MEAL_CORRECTED = "MealCorrected"
    MEAL_DELETED = "MealDeleted"
    ACTIVITY_LOGGED = "ActivityLogged"
    WATER_LOGGED = "WaterLogged"
    CALORIE_MILESTONE = "DailyCalorieMilestone"
    MACRO_CHANGE = "MeaningfulMacroChange"
    REPEATED_FOOD = "RepeatedFoodOrMeal"
    LOGGING_CHANGE = "LoggingBehaviorChange"
    AI_ACCURACY_CHANGE = "AIEstimationAccuracyChange"
    HABIT_OR_TREND = "HabitOrTrendDetected"
    EVIDENCE_SUFFICIENT = "EvidenceBecameSufficient"
    DAILY = "DailyEvaluation"
    WEEKLY = "WeeklyEvaluation"
    MONTHLY = "MonthlyEvaluation"


PERIODIC_TRIGGERS = {
    ProactiveTrigger.DAILY.value,
    ProactiveTrigger.WEEKLY.value,
    ProactiveTrigger.MONTHLY.value,
}


class InsightCandidate(BaseModel):
    """Verified, structured candidate. No generated prose belongs here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=16, max_length=64)
    type: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=50)
    trigger: str = Field(min_length=1, max_length=80)
    user_id: int = Field(gt=0)
    evidence: dict[str, Any]
    metrics: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    significance: float = Field(ge=0, le=1)
    usefulness: float = Field(ge=0, le=1)
    urgency: float = Field(default=0.2, ge=0, le=1)
    interruption_cost: float = Field(default=0.5, ge=0, le=1)
    direction: str = Field(default="neutral", pattern="^(positive|negative|neutral)$")
    dedup_key: str = Field(min_length=16, max_length=64)
    created_at: dt.datetime

    def verified_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


def pattern_direction(pattern: VerifiedPattern) -> str:
    explicit = pattern.payload.get("direction")
    if explicit == "improved":
        return "positive"
    if explicit == "worsened":
        return "negative"
    if pattern.id == "goal_adherence_change":
        return "positive" if pattern.payload.get("adherence_rate_change", 0) > 0 else "negative"
    return "neutral"


def pattern_trigger(pattern: VerifiedPattern) -> ProactiveTrigger:
    if pattern.category == "macros":
        return ProactiveTrigger.MACRO_CHANGE
    if pattern.category == "logging":
        return ProactiveTrigger.LOGGING_CHANGE
    if pattern.id in {"ai_estimation_accuracy", "ai_accuracy_trend"}:
        return ProactiveTrigger.AI_ACCURACY_CHANGE
    if pattern.confidence < 0.80:
        return ProactiveTrigger.EVIDENCE_SUFFICIENT
    return ProactiveTrigger.HABIT_OR_TREND


class CandidateFactory:
    @staticmethod
    def from_pattern(
        pattern: VerifiedPattern,
        *,
        user_id: int,
        detector_id: str,
        source_trigger: str,
        period_start: dt.date,
        period_end: dt.date,
        now: dt.datetime,
    ) -> InsightCandidate:
        trigger = pattern_trigger(pattern).value
        direction = pattern_direction(pattern)
        identity = {
            "user_id": user_id,
            "type": pattern.id,
            "trigger": trigger,
            "period_end": period_end,
            "metrics": pattern.payload,
        }
        candidate_hash = _hash(identity)
        return InsightCandidate(
            candidate_id=f"candidate_{candidate_hash[:40]}",
            type=pattern.id,
            category=pattern.category,
            trigger=trigger,
            user_id=user_id,
            evidence={
                "source_trigger": source_trigger,
                "detector_id": detector_id,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            },
            metrics=pattern.payload,
            confidence=pattern.confidence,
            novelty=pattern.novelty,
            relevance=pattern.user_relevance,
            significance=pattern.effect_size,
            usefulness=min(1.0, pattern.actionability * 0.7 + min(pattern.priority / 100, 1) * 0.3),
            urgency=0.55 if direction == "negative" else 0.25,
            interruption_cost=0.35 if pattern.priority >= 85 else 0.55,
            direction=direction,
            dedup_key=_hash([pattern.concept or pattern.id, direction])[:40],
            created_at=now,
        )

    @staticmethod
    def calorie_milestone(*, user_id: int, source_trigger: str, day: Any, now: dt.datetime) -> InsightCandidate | None:
        if day.goal_calories <= 0 or day.calories <= 0:
            return None
        ratio = day.calories / day.goal_calories
        if ratio > 1.05:
            milestone, label, significance = 1.05, "over_target", min(1.0, ratio - 1 + 0.55)
        elif ratio >= 0.90:
            milestone, label, significance = 0.90, "near_target", 0.62
        elif ratio >= 0.75:
            milestone, label, significance = 0.75, "75_percent", 0.48
        elif ratio >= 0.50:
            milestone, label, significance = 0.50, "50_percent", 0.38
        elif ratio >= 0.25:
            milestone, label, significance = 0.25, "25_percent", 0.30
        else:
            return None
        metrics = {
            "date": day.date.isoformat(),
            "consumed_calories": day.calories,
            "calorie_target": day.goal_calories,
            "target_fraction": round(ratio, 3),
            "milestone_fraction": milestone,
            "milestone": label,
        }
        candidate_hash = _hash([user_id, "daily_calorie_milestone", day.date, label])
        return InsightCandidate(
            candidate_id=f"candidate_{candidate_hash[:40]}",
            type="daily_calorie_milestone",
            category="calories",
            trigger=ProactiveTrigger.CALORIE_MILESTONE.value,
            user_id=user_id,
            evidence={"source_trigger": source_trigger, "date": day.date.isoformat()},
            metrics=metrics,
            confidence=1.0,
            novelty=0.66,
            relevance=0.75,
            significance=significance,
            usefulness=0.58,
            urgency=0.20 if label != "over_target" else 0.42,
            interruption_cost=0.72,
            direction="neutral",
            dedup_key=_hash(["daily_calorie_milestone", day.date])[:40],
            created_at=now,
        )

    @staticmethod
    def repeated_meal(
        *,
        user_id: int,
        source_trigger: str,
        meals: list[Any],
        period_start: dt.date,
        period_end: dt.date,
        now: dt.datetime,
    ) -> InsightCandidate | None:
        normalized: list[tuple[str, str, dt.date]] = []
        for meal in meals:
            name = (getattr(meal, "meal_name", None) or "").strip()
            if name:
                normalized.append((name.casefold(), name, meal.created_at.date()))
        counts = Counter(key for key, _, _ in normalized)
        if not counts:
            return None
        key, count = counts.most_common(1)[0]
        dates: dict[str, set[dt.date]] = defaultdict(set)
        display_names: dict[str, str] = {}
        for normalized_name, display_name, date in normalized:
            dates[normalized_name].add(date)
            display_names.setdefault(normalized_name, display_name)
        days_seen = len(dates[key])
        if count < 3 or days_seen < 2:
            return None
        metrics = {
            "meal_name": display_names[key],
            "times_logged": count,
            "distinct_days": days_seen,
            "period_days": (period_end - period_start).days + 1,
        }
        candidate_hash = _hash([user_id, "repeated_meal", key, count, days_seen, period_end])
        return InsightCandidate(
            candidate_id=f"candidate_{candidate_hash[:40]}",
            type="repeated_meal",
            category="meals",
            trigger=ProactiveTrigger.REPEATED_FOOD.value,
            user_id=user_id,
            evidence={
                "source_trigger": source_trigger,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            },
            metrics=metrics,
            confidence=min(0.96, 0.68 + count * 0.04 + days_seen * 0.03),
            novelty=0.78,
            relevance=0.82,
            significance=min(1.0, count / 7),
            usefulness=0.66,
            urgency=0.12,
            interruption_cost=0.68,
            direction="neutral",
            dedup_key=_hash(["repeated_meal", key])[:40],
            created_at=now,
        )
