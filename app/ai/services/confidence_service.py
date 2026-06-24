"""Deterministic confidence scoring.

Replaces the model-self-reported confidence string (the model grading its own
homework) with a float in [0, 1] derived AFTER validation from observable
signals. Zero tokens, fully reproducible. The float is bucketed into the
existing low/medium/high enum using the same thresholds as
MealResponse.validate_confidence (0.5 / 0.8), so the stored string stays
consistent with the score.
"""
import logging

from app.ai.schemas.meal_estimate import MealEstimateResult

logger = logging.getLogger("app.ai.confidence")

# Map a (model/ASR) qualitative label to a numeric ceiling.
_LABEL_TO_SCORE = {"low": 0.4, "medium": 0.7, "high": 0.95}


def bucket_confidence(score: float) -> str:
    """Bucket a [0,1] score into the low/medium/high enum (matches MealResponse)."""
    if score < 0.5:
        return "low"
    if score < 0.8:
        return "medium"
    return "high"


class AIConfidenceService:
    """Computes an evidence-based confidence score for a meal estimate."""

    @staticmethod
    def compute(
        result: MealEstimateResult,
        *,
        transcription_confidence: str | None = None,
    ) -> float:
        # Clarification / no-food: the estimate is intentionally empty.
        if result.needs_clarification or result.estimated_calories <= 0:
            return 0.1

        # A recovered/repaired extraction is structurally untrustworthy regardless
        # of how clean the numbers look.
        if result.degraded_extraction:
            return 0.25

        # Base prior by input modality.
        base = {"text": 0.62, "photo": 0.55, "voice": 0.55}.get(result.source_type, 0.58)
        score = base

        # 1. Uncertainty band width — the strongest signal the model already emits.
        if result.estimated_min_calories is not None and result.estimated_max_calories is not None:
            width_ratio = (result.estimated_max_calories - result.estimated_min_calories) / max(
                result.estimated_calories, 1
            )
            if width_ratio <= 0.25:
                score += 0.15
            elif width_ratio <= 0.5:
                score += 0.05
            elif width_ratio >= 1.0:
                score -= 0.20
            else:
                score -= 0.05
        else:
            # No band supplied: mild penalty (the model declined to bound itself).
            score -= 0.05

        # 2. Portion grounding — every item with both weight and density is a
        #    measured estimate; missing weights mean the model is guessing volume.
        if result.items:
            weighted = sum(
                1 for it in result.items if it.weight_grams and it.calories_per_100g
            )
            frac = weighted / len(result.items)
            score += 0.12 * frac - 0.10 * (1 - frac)
            # Very complex plates are harder to get right.
            if len(result.items) >= 6:
                score -= 0.05
        else:
            score -= 0.10

        # 3. Validation flags raised during normalization.
        if result.density_clamped:
            score -= 0.20
        if result.macro_mismatch:
            score -= 0.10
        if result.total_realigned:
            score -= 0.05

        score = max(0.05, min(0.98, score))

        # 4. Voice: a shaky transcript caps the whole estimate — you cannot be more
        #    confident about the calories than about what was said.
        if result.source_type == "voice" and transcription_confidence:
            cap = _LABEL_TO_SCORE.get(transcription_confidence.lower())
            if cap is not None:
                score = min(score, cap)

        return round(score, 3)
