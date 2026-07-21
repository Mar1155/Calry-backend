"""Deterministic memory distillers.

Each distiller turns confirmed meals / food memories into BeliefCandidates and
MomentSpecs. Distillers never compute confidence or apply gates — they supply the
raw value, evidence, and a domain-specific consistency scalar; the service applies
the reproducible confidence function and the materialization gate. No LLM is used.
"""

import datetime as dt
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.text_normalization import canonicalize_food_name
from app.memory.types import BeliefCandidate, DistillationResult, EvidenceRecord, MomentSpec
from app.memory.value_schemas import CalibrationValue, PortionValue, PreferenceValue
from app.models.food_memory import UserFoodMemory
from app.models.meal import Meal


@dataclass(frozen=True)
class DistillationContext:
    db: AsyncSession
    user_id: int
    now: dt.datetime
    source_versions: dict[str, int]
    suppressed_keys: set[tuple[str, str, str]]

    def is_suppressed(self, domain: str, concept: str, concept_key: str) -> bool:
        return (domain, concept, concept_key) in self.suppressed_keys


class MemoryDistiller(ABC):
    registry: list[type["MemoryDistiller"]] = []
    distiller_id = "base"
    distiller_version = "1.0"

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstractmethods__", None):
            MemoryDistiller.registry.append(cls)

    @abstractmethod
    async def distill(self, ctx: DistillationContext) -> DistillationResult: ...


# --- helpers ----------------------------------------------------------------


async def _confirmed_meals(ctx: DistillationContext) -> list[Meal]:
    result = await ctx.db.execute(
        select(Meal)
        .where(Meal.user_id == ctx.user_id, Meal.confirmed_calories.isnot(None))
        .options(selectinload(Meal.items))
        .order_by(Meal.created_at)
    )
    return list(result.scalars().all())


def _meal_observed_at(meal: Meal) -> dt.datetime:
    return meal.confirmed_at or meal.created_at


def _meal_total_grams(meal: Meal) -> int:
    return sum(item.weight_grams for item in meal.items)


def _is_correction(meal: Meal) -> bool:
    return bool(meal.correction_delta)


def _evidence_type(meal: Meal) -> str:
    return "correction" if _is_correction(meal) else "confirmation"


def _median(values: list[int]) -> int:
    return int(round(statistics.median(values))) if values else 0


def _quantile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return int(round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction))


def _consistency_from_spread(values: list[int]) -> float:
    """1.0 when portions barely vary, decaying with coefficient of variation."""
    if len(values) < 2:
        return 0.5
    mean = statistics.fmean(values)
    if mean <= 0:
        return 0.0
    stdev = statistics.pstdev(values)
    cv = stdev / mean
    return round(max(0.0, min(1.0, 1.0 - cv)), 4)


# --- portion ----------------------------------------------------------------


class PortionDistiller(MemoryDistiller):
    distiller_id = "portion"
    distiller_version = "1.0"

    async def distill(self, ctx: DistillationContext) -> DistillationResult:
        meals = await _confirmed_meals(ctx)
        by_key: dict[str, list[Meal]] = {}
        for meal in meals:
            key = canonicalize_food_name(meal.meal_name or meal.original_input)
            if key and _meal_total_grams(meal) > 0:
                by_key.setdefault(key, []).append(meal)

        candidates: list[BeliefCandidate] = []
        for key, group in by_key.items():
            concept_key = key[:160]
            if ctx.is_suppressed("portion_model", "food_portion", concept_key):
                continue
            grams = [_meal_total_grams(meal) for meal in group]
            central = _median(grams)
            display_name = group[-1].meal_name or group[-1].original_input
            observed = [_meal_observed_at(meal) for meal in group]
            span_days = (max(observed) - min(observed)).days if len(observed) > 1 else 0

            evidence = [
                EvidenceRecord(
                    evidence_type=_evidence_type(meal),
                    ref_table="meals",
                    ref_id=meal.id,
                    observed_at=_meal_observed_at(meal),
                    detail={
                        "grams": _meal_total_grams(meal),
                        "correction_delta": meal.correction_delta or 0,
                        "correction_percent": round(meal.correction_percent or 0.0, 2),
                    },
                )
                for meal in group
            ]
            value = PortionValue(
                canonical_key=key,
                display_name=display_name[:500],
                grams=central,
                grams_low=max(1, _quantile(grams, 0.25)),
                grams_high=max(1, _quantile(grams, 0.75)),
                sample_count=len(group),
            )
            formation = MomentSpec(
                moment_kind="discovery",
                domain="portion_model",
                beat_key="formed",
                fact={
                    "display_name": display_name[:120],
                    "grams": central,
                    "sample_count": len(group),
                    "span_days": span_days,
                },
                confidence_at=0.0,  # filled by the service once confidence is known
                evidence_span_days=span_days,
                occurred_on=ctx.now.date(),
            )
            candidates.append(
                BeliefCandidate(
                    domain="portion_model",
                    concept="food_portion",
                    concept_key=concept_key,
                    value=value,
                    evidence=evidence,
                    consistency=_consistency_from_spread(grams),
                    distiller_id=self.distiller_id,
                    distiller_version=self.distiller_version,
                    source_versions=ctx.source_versions,
                    formation_moment=formation,
                )
            )
        return DistillationResult(belief_candidates=candidates)


# --- preference -------------------------------------------------------------


class PreferenceDistiller(MemoryDistiller):
    distiller_id = "preference"
    distiller_version = "1.0"

    async def distill(self, ctx: DistillationContext) -> DistillationResult:
        meals = await _confirmed_meals(ctx)
        by_key: dict[str, list[Meal]] = {}
        for meal in meals:
            key = canonicalize_food_name(meal.meal_name or meal.original_input)
            if key:
                by_key.setdefault(key, []).append(meal)

        fm_result = await ctx.db.execute(select(UserFoodMemory).where(UserFoodMemory.user_id == ctx.user_id))
        food_memories = {fm.canonical_key: fm for fm in fm_result.scalars().all() if fm.canonical_key}

        candidates: list[BeliefCandidate] = []
        for key, group in by_key.items():
            concept_key = key[:160]
            if ctx.is_suppressed("preference", "regular_food", concept_key):
                continue
            distinct_days = {_meal_observed_at(meal).date() for meal in group}
            food_memory = food_memories.get(key)
            is_favourite = bool(food_memory.is_favorite) if food_memory else False
            occurrences = len(group)

            if is_favourite:
                preference_type = "favourite"
            elif occurrences >= settings.MEMORY_PREFERENCE_REGULAR_OCCURRENCES and len(distinct_days) >= settings.MEMORY_PREFERENCE_REGULAR_DAYS:
                preference_type = "regular"
            else:
                continue  # not strongly evidenced

            display_name = group[-1].meal_name or group[-1].original_input
            observed = [_meal_observed_at(meal) for meal in group]
            span_days = (max(observed) - min(observed)).days if len(observed) > 1 else 0

            evidence = [
                EvidenceRecord(
                    evidence_type=_evidence_type(meal),
                    ref_table="meals",
                    ref_id=meal.id,
                    observed_at=_meal_observed_at(meal),
                    detail={"meal_name": (meal.meal_name or "")[:120]},
                )
                for meal in group
            ]
            if food_memory is not None:
                evidence.append(
                    EvidenceRecord(
                        evidence_type="food_memory",
                        ref_table="user_food_memory",
                        ref_id=food_memory.id,
                        observed_at=food_memory.last_used_at,
                        detail={"use_count": food_memory.use_count, "is_favorite": bool(food_memory.is_favorite)},
                    )
                )

            value = PreferenceValue(
                canonical_key=key,
                display_name=display_name[:500],
                preference_type=preference_type,
                occurrences=occurrences,
                distinct_days=len(distinct_days),
            )
            formation = MomentSpec(
                moment_kind="discovery",
                domain="preference",
                beat_key="formed",
                fact={
                    "display_name": display_name[:120],
                    "preference_type": preference_type,
                    "occurrences": occurrences,
                    "distinct_days": len(distinct_days),
                },
                confidence_at=0.0,
                evidence_span_days=span_days,
                occurred_on=ctx.now.date(),
            )
            candidates.append(
                BeliefCandidate(
                    domain="preference",
                    concept="regular_food",
                    concept_key=concept_key,
                    value=value,
                    evidence=evidence,
                    consistency=round(min(1.0, len(distinct_days) / 8.0), 4),
                    distiller_id=self.distiller_id,
                    distiller_version=self.distiller_version,
                    source_versions=ctx.source_versions,
                    formation_moment=formation,
                )
            )
        return DistillationResult(belief_candidates=candidates)


# --- calibration ------------------------------------------------------------


class CalibrationDistiller(MemoryDistiller):
    distiller_id = "calibration"
    distiller_version = "1.0"

    async def distill(self, ctx: DistillationContext) -> DistillationResult:
        meals = await _confirmed_meals(ctx)
        if not meals:
            return DistillationResult()

        scopes: dict[str, list[Meal]] = {"overall": list(meals)}
        for meal in meals:
            scopes.setdefault(f"source:{meal.source_type}", []).append(meal)

        candidates: list[BeliefCandidate] = []
        for scope, group in scopes.items():
            concept_key = scope[:160]
            if ctx.is_suppressed("ai_calibration", "estimation_accuracy", concept_key):
                continue
            sample_count = len(group)
            no_edit = [m for m in group if not _is_correction(m)]
            within5 = [m for m in group if abs(m.correction_percent or 0.0) <= 5.0]
            abs_pcts = [abs(m.correction_percent or 0.0) for m in group]
            no_edit_rate = len(no_edit) / sample_count
            within5_rate = len(within5) / sample_count
            observed = [_meal_observed_at(m) for m in group]
            span_days = (max(observed) - min(observed)).days if len(observed) > 1 else 0

            evidence = [
                EvidenceRecord(
                    evidence_type=_evidence_type(m),
                    ref_table="meals",
                    ref_id=m.id,
                    observed_at=_meal_observed_at(m),
                    detail={
                        "correction_delta": m.correction_delta or 0,
                        "correction_percent": round(m.correction_percent or 0.0, 2),
                    },
                )
                for m in group
            ]
            value = CalibrationValue(
                scope=scope,
                sample_count=sample_count,
                confirmed_without_edit_count=len(no_edit),
                no_edit_rate=round(no_edit_rate, 4),
                within_5pct_count=len(within5),
                within_5pct_rate=round(within5_rate, 4),
                median_abs_correction_percent=round(float(statistics.median(abs_pcts)) if abs_pcts else 0.0, 2),
            )

            extra: list[MomentSpec] = []
            if no_edit_rate >= settings.MEMORY_CALIBRATION_NO_EDIT_RATE:
                extra.append(
                    MomentSpec(
                        moment_kind="learning",
                        domain="ai_calibration",
                        beat_key=f"cal_learn_{scope}",
                        fact={"scope": scope, "no_edit_rate": round(no_edit_rate, 3), "sample_count": sample_count},
                        confidence_at=0.0,
                        evidence_span_days=span_days,
                        occurred_on=ctx.now.date(),
                    )
                )
            if within5_rate >= settings.MEMORY_CALIBRATION_WITHIN5_RATE:
                extra.append(
                    MomentSpec(
                        moment_kind="calibration",
                        domain="ai_calibration",
                        beat_key=f"cal_within5_{scope}",
                        fact={"scope": scope, "within_5pct_rate": round(within5_rate, 3), "sample_count": sample_count},
                        confidence_at=0.0,
                        evidence_span_days=span_days,
                        occurred_on=ctx.now.date(),
                    )
                )

            candidates.append(
                BeliefCandidate(
                    domain="ai_calibration",
                    concept="estimation_accuracy",
                    concept_key=concept_key,
                    value=value,
                    evidence=evidence,
                    consistency=round(max(no_edit_rate, within5_rate), 4),
                    distiller_id=self.distiller_id,
                    distiller_version=self.distiller_version,
                    source_versions=ctx.source_versions,
                    formation_moment=None,  # calibration surfaces via band moments, not a formation card
                    extra_moments=extra,
                )
            )
        return DistillationResult(belief_candidates=candidates)


# --- milestones -------------------------------------------------------------

_MEAL_MILESTONES = (20, 50, 100, 200)
_DAY_MILESTONES = (30, 180, 365)
_FOOD_MILESTONES = (10, 25, 50)


class MilestoneDistiller(MemoryDistiller):
    distiller_id = "milestone"
    distiller_version = "1.0"

    async def distill(self, ctx: DistillationContext) -> DistillationResult:
        meals = await _confirmed_meals(ctx)
        if not meals:
            return DistillationResult()

        moments: list[MomentSpec] = []
        confirmed_count = len(meals)
        first_day = _meal_observed_at(meals[0]).date()
        days_together = (ctx.now.date() - first_day).days
        distinct_foods = {canonicalize_food_name(m.meal_name or m.original_input) for m in meals}
        distinct_foods.discard("")
        foods_learned = len(distinct_foods)
        span_days = days_together

        for threshold in _MEAL_MILESTONES:
            if confirmed_count >= threshold:
                moments.append(
                    MomentSpec(
                        moment_kind="milestone",
                        domain="relationship",
                        beat_key=f"m_meals_{threshold}",
                        fact={"milestone": "meals_confirmed", "count": threshold},
                        confidence_at=1.0,
                        evidence_span_days=span_days,
                        occurred_on=_meal_observed_at(meals[threshold - 1]).date(),
                    )
                )
        for threshold in _DAY_MILESTONES:
            if days_together >= threshold:
                moments.append(
                    MomentSpec(
                        moment_kind="milestone",
                        domain="relationship",
                        beat_key=f"m_days_{threshold}",
                        fact={"milestone": "days_together", "count": threshold},
                        confidence_at=1.0,
                        evidence_span_days=span_days,
                        occurred_on=ctx.now.date(),
                    )
                )
        for threshold in _FOOD_MILESTONES:
            if foods_learned >= threshold:
                moments.append(
                    MomentSpec(
                        moment_kind="milestone",
                        domain="relationship",
                        beat_key=f"m_foods_{threshold}",
                        fact={"milestone": "foods_learned", "count": threshold},
                        confidence_at=1.0,
                        evidence_span_days=span_days,
                        occurred_on=ctx.now.date(),
                    )
                )
        return DistillationResult(standalone_moments=moments)
