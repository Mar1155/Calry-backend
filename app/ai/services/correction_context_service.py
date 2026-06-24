import logging
import statistics
from dataclasses import dataclass, field

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.meal import Meal

logger = logging.getLogger("app.ai.correction_context_service")

# C11 systematic-bias tuning.
_BIAS_MIN_MEALS = 5          # need at least this many corrected meals per source
_BIAS_MIN_PERCENT = 8.0      # only correct when the systematic bias is this large
_BIAS_MAX_FRACTION = 0.30    # never shift an estimate by more than +/-30%
_BIAS_SHRINK_K = 5           # shrinkage constant: trust grows with n/(n+k)


@dataclass
class CorrectionContext:
    """Everything the meal-logging path needs about a user's correction history,
    derived from ONE database query."""

    summary: str | None = None
    # Per source_type ("text"/"photo"/"voice") -> signed multiplier fraction to
    # apply to a fresh estimate, already shrinkage-weighted and clamped. Empty
    # when there is not enough evidence.
    bias_by_source: dict[str, float] = field(default_factory=dict)


class AICorrectionContextService:
    """Service to compile recent meal calibration history for a user to personalize AI estimations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _recent_confirmed_meals(self, user_id: int, limit: int) -> list[Meal]:
        stmt = (
            select(Meal)
            .where(Meal.user_id == user_id, Meal.confirmed_calories.isnot(None))
            .order_by(desc(Meal.created_at))
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _summarize(meals: list[Meal]) -> str | None:
        if not meals:
            return None

        total_pct_diff = 0.0
        total_delta = 0
        corrected_count = 0
        examples = []

        for m in meals:
            delta = m.correction_delta or 0
            pct = m.correction_percent or 0.0

            total_delta += delta
            total_pct_diff += pct
            if delta != 0:
                corrected_count += 1

            if len(examples) < 5:
                status = "corrected to" if delta != 0 else "confirmed exactly at"
                examples.append(
                    f"- Meal '{m.meal_name or 'Unknown'}': AI estimated {m.estimated_calories} kcal, "
                    f"user {status} {m.confirmed_calories} kcal ({pct:+.1f}%)"
                )

        avg_pct = total_pct_diff / len(meals)
        avg_delta = total_delta / len(meals)

        summary_lines = [
            f"User has calibrated/confirmed {len(meals)} meals recently.",
            f"Overall correction patterns: average change of {avg_pct:+.1f}% ({avg_delta:+.1f} kcal) per meal.",
            f"Out of {len(meals)} meals, user modified the estimate in {corrected_count} cases.",
            "Recent confirmation history:",
        ] + examples

        return "\n".join(summary_lines)

    @classmethod
    def _bias_by_source(cls, meals: list[Meal]) -> dict[str, float]:
        """Compute a robust, shrinkage-weighted multiplier fraction per source type.

        Uses the MEDIAN signed correction percent so a single mis-tap cannot swing
        the bias, and averages SEPARATELY per source type so photo portion error
        does not cancel text error. Excludes meals where correction was impossible
        (estimated_calories <= 0)."""
        by_source: dict[str, list[float]] = {}
        for m in meals:
            if (m.estimated_calories or 0) <= 0 or m.correction_percent is None:
                continue
            by_source.setdefault(m.source_type, []).append(m.correction_percent)

        out: dict[str, float] = {}
        for source, pcts in by_source.items():
            n = len(pcts)
            if n < _BIAS_MIN_MEALS:
                continue
            median_pct = statistics.median(pcts)
            if abs(median_pct) < _BIAS_MIN_PERCENT:
                continue
            raw = max(-_BIAS_MAX_FRACTION, min(_BIAS_MAX_FRACTION, median_pct / 100.0))
            shrink = n / (n + _BIAS_SHRINK_K)
            fraction = raw * shrink
            if abs(fraction) >= 0.01:  # ignore negligible shifts
                out[source] = round(fraction, 4)
        return out

    async def get_correction_context(self, user_id: int, limit: int = 30) -> CorrectionContext:
        """Single-query correction context for the meal-logging path: prose summary
        for the prompt AND the deterministic per-source bias for post-estimation
        correction. Replaces the two overlapping queries the route used to run."""
        try:
            meals = await self._recent_confirmed_meals(user_id, limit)
        except Exception as e:
            logger.error(f"Failed to load correction context: {e}")
            return CorrectionContext()

        # The prompt summary intentionally uses the 10 most recent for brevity, the
        # bias uses the full window for statistical stability.
        return CorrectionContext(
            summary=self._summarize(meals[:10]),
            bias_by_source=self._bias_by_source(meals),
        )

    async def get_user_correction_summary(self, user_id: int, limit: int = 10) -> str | None:
        """Compiles a summary of recent meal calorie corrections by the user."""
        try:
            meals = await self._recent_confirmed_meals(user_id, limit)
            return self._summarize(meals)
        except Exception as e:
            logger.error(f"Failed to generate correction summary: {e}")
            return None

    async def get_average_correction_percent(self, user_id: int, limit: int = 20) -> float | None:
        """Returns the average correction percentage directly."""
        try:
            meals = await self._recent_confirmed_meals(user_id, limit)
            if not meals:
                return None
            total_pct = sum(m.correction_percent or 0.0 for m in meals)
            return total_pct / len(meals)
        except Exception as e:
            logger.error(f"Failed to calculate average correction percent: {e}")
            return None
