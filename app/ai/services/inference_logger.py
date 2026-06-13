import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.inference import AIInferenceLog
from app.repositories.inference import AIInferenceLogRepository

logger = logging.getLogger("app.ai.inference_logger")


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
    ) -> None:
        try:
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
            )
            await self.repo.create(log_entry)
            await self.db.flush()
        except Exception as e:
            # We fail silently to prevent logger errors from blocking core user flows
            logger.error(f"Inference logging failed: {e}")
