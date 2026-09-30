import logging
from typing import TYPE_CHECKING

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inference import AIInferenceLog
from app.repositories.inference import AIInferenceLogRepository

if TYPE_CHECKING:
    from app.ai.schemas.meal_estimate import MealEstimateResult

logger = logging.getLogger("app.ai.inference_logger")


def _quality_fields(result: "MealEstimateResult | None") -> dict:
    """Scan audit (C27): post-validation signals that are not persisted on the
    meal, captured so an admin can filter and diagnose a scan later."""
    if result is None:
        return {}
    return {
        "item_count": len(result.items),
        "estimated_calories": result.estimated_calories,
        "confidence_score": result.confidence_score,
        "degraded_extraction": result.degraded_extraction,
        "needs_clarification": result.needs_clarification,
        "quality_json": {
            "density_clamped": result.density_clamped,
            "macro_mismatch": result.macro_mismatch,
            "total_realigned": result.total_realigned,
            "bias_applied": result.bias_applied,
            "assumptions": list(result.assumptions or []),
        },
    }


class AIInferenceLogger:
    """Service to log downstream AI model calls and metadata to the database."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AIInferenceLogRepository(db)

    async def log_call(
        self,
        user_id: int | None,
        provider: str,
        model_name: str,
        prompt_version: str,
        input_type: str,
        raw_input: str,
        raw_output: str | None,
        latency_ms: int,
        success: bool,
        error_message: str | None = None,
        token_usage: dict | None = None,
        finish_reason: str | None = None,
        result: "MealEstimateResult | None" = None,
        meal_id: int | None = None,
    ) -> int | None:
        """Persist one call. Returns the log id, or None when logging failed.

        When ``result`` is given its quality signals are captured and the new
        id is appended to ``result.linked_inference_log_ids`` so the caller can
        link the row to the meal it persists.
        """
        usage = token_usage or {}
        try:
            # Keep observability best-effort without poisoning the meal's outer
            # transaction when a log insert fails.
            async with self.db.begin_nested():
                log_entry = AIInferenceLog(
                    user_id=user_id,
                    provider=provider,
                    model_name=model_name,
                    prompt_version=prompt_version,
                    input_type=input_type,
                    raw_input=raw_input,
                    raw_output=raw_output,
                    latency_ms=latency_ms,
                    success=success,
                    error_message=error_message,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    cached_tokens=usage.get("cached_tokens"),
                    # C26: "length" means the provider cut the completion off at
                    # max_completion_tokens — queryable here instead of only
                    # inferable after the fact from a collapsed single-item result.
                    finish_reason=finish_reason,
                    meal_id=meal_id,
                    **_quality_fields(result if success else None),
                )
                await self.repo.create(log_entry)
            if result is not None and log_entry.id is not None:
                result.linked_inference_log_ids.append(log_entry.id)
            return log_entry.id
        except Exception as e:
            # We fail silently to prevent logger errors from blocking core user flows
            logger.error(f"Inference logging failed: {e}")
            return None

    async def link_to_meal(self, log_ids: list[int], meal_id: int) -> None:
        """Attach already-written log rows to the meal they produced."""
        ids = [log_id for log_id in log_ids if log_id is not None]
        if not ids:
            return
        try:
            async with self.db.begin_nested():
                await self.db.execute(
                    update(AIInferenceLog).where(AIInferenceLog.id.in_(ids)).values(meal_id=meal_id)
                )
        except Exception as e:
            logger.error(f"Inference log linking failed: {e}")
