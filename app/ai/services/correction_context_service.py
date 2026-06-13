import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc
from app.models.meal import Meal

logger = logging.getLogger("app.ai.correction_context_service")


class AICorrectionContextService:
    """Service to compile recent meal calibration history for a user to personalize AI estimations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_user_correction_summary(self, user_id: int, limit: int = 10) -> str | None:
        """Compiles a summary of recent meal calorie corrections by the user."""
        try:
            # Select recent meals that the user has confirmed/calibrated
            stmt = (
                select(Meal)
                .where(Meal.user_id == user_id, Meal.confirmed_calories.isnot(None))
                .order_by(desc(Meal.created_at))
                .limit(limit)
            )
            result = await self.db.execute(stmt)
            meals = list(result.scalars().all())

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

                # Gather a few specific examples for the prompt context
                if len(examples) < 5:
                    status = "corrected to" if delta != 0 else "confirmed exactly at"
                    examples.append(
                        f"- Meal '{m.meal_name or 'Unknown'}': AI estimated {m.estimated_calories} kcal, "
                        f"user {status} {m.confirmed_calories} kcal ({pct:+.1f}%)"
                    )

            avg_pct = total_pct_diff / len(meals) if meals else 0.0
            avg_delta = total_delta / len(meals) if meals else 0.0

            summary_lines = [
                f"User has calibrated/confirmed {len(meals)} meals recently.",
                f"Overall correction patterns: average change of {avg_pct:+.1f}% ({avg_delta:+.1f} kcal) per meal.",
                f"Out of {len(meals)} meals, user modified the estimate in {corrected_count} cases.",
                "Recent confirmation history:"
            ] + examples

            return "\n".join(summary_lines)

        except Exception as e:
            logger.error(f"Failed to generate correction summary: {e}")
            return None

    async def get_average_correction_percent(self, user_id: int, limit: int = 20) -> float | None:
        """Returns the average correction percentage directly."""
        try:
            stmt = (
                select(Meal)
                .where(Meal.user_id == user_id, Meal.confirmed_calories.isnot(None))
                .order_by(desc(Meal.created_at))
                .limit(limit)
            )
            result = await self.db.execute(stmt)
            meals = list(result.scalars().all())

            if not meals:
                return None

            total_pct = sum(m.correction_percent or 0.0 for m in meals)
            return total_pct / len(meals)
        except Exception as e:
            logger.error(f"Failed to calculate average correction percent: {e}")
            return None
