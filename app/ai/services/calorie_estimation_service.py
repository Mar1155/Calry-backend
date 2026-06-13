import logging
import time
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import BaseAIProvider
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.meal_estimate import MealEstimateResult, UserContext
from app.ai.schemas.meal_completion import MealCompletionResult, MealCompletionRequest
from app.ai.services.validation_service import AIValidationService
from app.ai.services.inference_logger import AIInferenceLogger
from app.ai.services.speech_service import AISpeechService
from app.core.config import settings

logger = logging.getLogger("app.ai.calorie_estimation_service")


class AICalorieEstimationService:
    """Orchestrator for meal calorie estimations across text, vision, and voice inputs using OpenRouter."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.inference_logger = AIInferenceLogger(db)
        self.speech_service = AISpeechService(db)
        
        # Register OpenRouter as the sole provider
        self.providers: dict[str, BaseAIProvider] = {
            "openrouter": OpenRouterProvider(),
        }

    def _get_provider(self, provider_override: str | None = None) -> BaseAIProvider:
        return self.providers["openrouter"]

    async def estimate_from_text(
        self,
        text: str,
        user_context: UserContext | None = None,
        user_id: int | None = None,
        provider_override: str | None = None,
    ) -> MealEstimateResult:
        provider = self._get_provider(provider_override)
        start_time = time.perf_counter()
        success = False
        raw_output = None
        error_msg = None
        
        try:
            raw_result = await provider.estimate_meal_from_text(text, user_context)
            raw_output = raw_result.raw_output
            
            # Validate and normalize
            validated_result = AIValidationService.validate_and_normalize_estimate(raw_result)
            success = True
            return validated_result
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in estimate_from_text: {e}")
            raise e
        finally:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            model_name = settings.OPENROUTER_TEXT_MODEL
            prompt_version = "text_meal_estimation_v1"
            await self.inference_logger.log_call(
                user_id=user_id,
                provider=provider.provider_name,
                model_name=model_name,
                prompt_version=prompt_version,
                input_type="text",
                raw_input=text,
                raw_output=str(raw_output) if raw_output else None,
                latency_ms=latency_ms,
                success=success,
                error_message=error_msg,
            )

    async def estimate_from_image(
        self,
        image_url: str,
        user_context: UserContext | None = None,
        optional_hint: str | None = None,
        user_id: int | None = None,
        provider_override: str | None = None,
    ) -> MealEstimateResult:
        provider = self._get_provider(provider_override)
        start_time = time.perf_counter()
        success = False
        raw_output = None
        error_msg = None
        
        try:
            raw_result = await provider.estimate_meal_from_image(image_url, user_context, optional_hint)
            raw_output = raw_result.raw_output
            
            # Validate and normalize
            validated_result = AIValidationService.validate_and_normalize_estimate(raw_result)
            success = True
            return validated_result
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in estimate_from_image: {e}")
            raise e
        finally:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            model_name = settings.OPENROUTER_IMAGE_MODEL
            prompt_version = "image_meal_estimation_v1"
            await self.inference_logger.log_call(
                user_id=user_id,
                provider=provider.provider_name,
                model_name=model_name,
                prompt_version=prompt_version,
                input_type="photo",
                raw_input=f"Image URL: {image_url} | Hint: {optional_hint or 'None'}",
                raw_output=str(raw_output) if raw_output else None,
                latency_ms=latency_ms,
                success=success,
                error_message=error_msg,
            )

    async def estimate_from_voice(
        self,
        audio_url: str,
        user_context: UserContext | None = None,
        user_id: int | None = None,
        provider_override: str | None = None,
    ) -> tuple[str, MealEstimateResult]:
        """Transcribes the voice input first, and then runs the text calorie estimation pipeline."""
        # 1. Transcribe voice audio
        transcription_result = await self.speech_service.transcribe_audio(
            audio_url=audio_url,
            user_id=user_id,
            provider_override=provider_override,
        )
        
        transcript = transcription_result.transcript
        
        # 2. Run text calorie estimation on the transcript
        estimation_result = await self.estimate_from_text(
            text=transcript,
            user_context=user_context,
            user_id=user_id,
            provider_override=provider_override,
        )
        
        return transcript, estimation_result

    async def suggest_meal_completion(
        self,
        completion_req: MealCompletionRequest,
        user_context: UserContext | None = None,
        user_id: int | None = None,
        provider_override: str | None = None,
    ) -> MealCompletionResult:
        provider = self._get_provider(provider_override)
        start_time = time.perf_counter()
        success = False
        raw_output = None
        error_msg = None
        
        try:
            raw_result = await provider.suggest_meal_completion(completion_req, user_context)
            raw_output = raw_result.raw_output
            success = True
            return raw_result
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in suggest_meal_completion: {e}")
            raise e
        finally:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            model_name = settings.OPENROUTER_TEXT_MODEL
            prompt_version = "meal_completion_v1"
            await self.inference_logger.log_call(
                user_id=user_id,
                provider=provider.provider_name,
                model_name=model_name,
                prompt_version=prompt_version,
                input_type="meal_completion",
                raw_input=completion_req.model_dump_json(),
                raw_output=str(raw_output) if raw_output else None,
                latency_ms=latency_ms,
                success=success,
                error_message=error_msg,
            )
