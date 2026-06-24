import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.image_estimation import IMAGE_MEAL_ESTIMATION_PROMPT_VERSION
from app.ai.prompts.meal_completion import MEAL_COMPLETION_PROMPT_VERSION
from app.ai.prompts.meal_estimation import TEXT_MEAL_ESTIMATION_PROMPT_VERSION
from app.ai.prompts.meal_refinement import MEAL_REFINEMENT_PROMPT_VERSION
from app.ai.providers.base import BaseAIProvider
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.meal_completion import MealCompletionRequest, MealCompletionResult
from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult, UserContext
from app.ai.services.confidence_service import AIConfidenceService, bucket_confidence
from app.ai.services.inference_logger import AIInferenceLogger
from app.ai.services.speech_service import AISpeechService
from app.ai.services.validation_service import AIValidationService
from app.core.config import settings
from app.models.food_memory import UserFoodMemory
from app.repositories.food_memory import FoodMemoryRepository

logger = logging.getLogger("app.ai.calorie_estimation_service")


class AICalorieEstimationService:
    """Orchestrator for meal calorie estimations across text, vision, and voice inputs using OpenRouter."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.inference_logger = AIInferenceLogger(db)
        self.speech_service = AISpeechService(db)
        self.food_memory = FoodMemoryRepository(db)

        # Register OpenRouter as the sole provider
        self.providers: dict[str, BaseAIProvider] = {
            "openrouter": OpenRouterProvider(),
        }

    def _get_provider(self, provider_override: str | None = None) -> BaseAIProvider:
        return self.providers["openrouter"]

    # ---- deterministic post-processing (C11 bias + C12 confidence) ----------

    @staticmethod
    def _apply_bias(result: MealEstimateResult, user_context: UserContext | None, channel: str) -> None:
        """C11: shift a fresh estimate by the user's systematic per-source bias.
        Never applied to clarifications or empty estimates."""
        if result.needs_clarification or result.estimated_calories <= 0:
            return
        if not user_context or not user_context.correction_bias_by_source:
            return
        frac = user_context.correction_bias_by_source.get(channel)
        if not frac:
            return

        mult = 1 + frac
        result.estimated_calories = max(0, round(result.estimated_calories * mult))
        for it in result.items:
            if it.calories_per_100g is not None:
                it.calories_per_100g = round(it.calories_per_100g * mult, 1)
            it.estimated_calories = max(0, round(it.estimated_calories * mult))
            for attr in ("protein_g", "carbs_g", "fat_g"):
                val = getattr(it, attr)
                if val is not None:
                    setattr(it, attr, round(val * mult, 1))
        for attr in ("total_protein_g", "total_carbs_g", "total_fat_g",
                     "estimated_min_calories", "estimated_max_calories"):
            val = getattr(result, attr)
            if val is not None:
                setattr(result, attr, round(val * mult, 1) if "_g" in attr else round(val * mult))
        result.assumptions.append("Adjusted for your recent correction trend.")

    def _finalize(
        self,
        result: MealEstimateResult,
        user_context: UserContext | None,
        channel: str,
        transcription_confidence: str | None = None,
    ) -> MealEstimateResult:
        # The channel is the source of truth for both bias and confidence base.
        result.source_type = channel  # type: ignore[assignment]
        self._apply_bias(result, user_context, channel)
        score = AIConfidenceService.compute(result, transcription_confidence=transcription_confidence)
        result.confidence_score = score
        result.confidence = bucket_confidence(score)  # type: ignore[assignment]
        return result

    # ---- pre-inference cache (C3) -------------------------------------------

    @staticmethod
    def _synthesize_from_memory(memory: UserFoodMemory, channel: str) -> MealEstimateResult:
        """Build a result from a user-confirmed food memory — no LLM, no jitter."""
        cal = memory.learned_calories
        items: list[MealEstimateItem] = []
        for snap in (memory.items_snapshot or []):
            items.append(
                MealEstimateItem(
                    name=snap.get("name", memory.display_name),
                    quantity_estimate=snap.get("quantity_estimate"),
                    weight_grams=snap.get("weight_grams"),
                    calories_per_100g=snap.get("calories_per_100g"),
                    protein_g=snap.get("protein_g"),
                    carbs_g=snap.get("carbs_g"),
                    fat_g=snap.get("fat_g"),
                    estimated_calories=snap.get("estimated_calories", 0) or 0,
                )
            )
        return MealEstimateResult(
            meal_name=memory.display_name,
            estimated_calories=cal,
            estimated_min_calories=round(cal * 0.95),
            estimated_max_calories=round(cal * 1.05),
            confidence="high",
            confidence_score=0.9,
            source_type=channel,  # type: ignore[arg-type]
            items=items,
            assumptions=["Served from your confirmed history."],
            needs_clarification=False,
            total_protein_g=memory.protein_g,
            total_carbs_g=memory.carbs_g,
            total_fat_g=memory.fat_g,
            model_name="cache",
            prompt_version="food_memory_cache_v1",
            raw_output=None,
            latency_ms=0,
        )

    async def _try_cache(self, user_id: int, text: str, channel: str) -> MealEstimateResult | None:
        if not settings.FOOD_MEMORY_CACHE_ENABLED:
            return None
        try:
            memory = await self.food_memory.get_cached_match(user_id, text)
        except Exception as e:
            logger.warning(f"Food-memory cache lookup failed: {e}")
            return None
        if memory is None:
            return None

        result = self._synthesize_from_memory(memory, channel)
        # Log a cache-served row so analytics don't show a phantom drop in calls (C3 verdict).
        await self.inference_logger.log_call(
            user_id=user_id,
            provider="cache",
            model_name="food_memory_cache",
            prompt_version="food_memory_cache_v1",
            input_type=f"{channel}_cache",
            raw_input=text,
            raw_output=f"memory_id={memory.id}",
            latency_ms=0,
            success=True,
        )
        logger.info(f"Food-memory cache hit for user {user_id} (memory_id={memory.id}); skipped LLM.")
        return result

    # ---- public API ---------------------------------------------------------

    async def estimate_from_text(
        self,
        text: str,
        user_context: UserContext | None = None,
        user_id: int | None = None,
        provider_override: str | None = None,
        *,
        is_voice: bool = False,
        channel: str = "text",
        transcription_confidence: str | None = None,
        additional_context: str | None = None,
    ) -> MealEstimateResult:
        # 1. Pre-inference cache: serve confirmed repeat foods without an LLM call.
        if user_id is not None and not (additional_context and additional_context.strip()):
            cached = await self._try_cache(user_id, text, channel)
            if cached is not None:
                return cached

        provider = self._get_provider(provider_override)
        start_time = time.perf_counter()
        success = False
        raw_result = None
        raw_output = None
        error_msg = None

        try:
            raw_result = await provider.estimate_meal_from_text(
                text,
                user_context,
                is_voice=is_voice,
                additional_context=additional_context,
            )
            raw_output = raw_result.raw_output

            validated_result = AIValidationService.validate_and_normalize_estimate(raw_result)
            self._finalize(validated_result, user_context, channel, transcription_confidence)
            success = True
            return validated_result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in estimate_from_text: {e}")
            raise e
        finally:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            prompt_version = (
                raw_result.prompt_version if raw_result is not None else TEXT_MEAL_ESTIMATION_PROMPT_VERSION
            )
            await self.inference_logger.log_call(
                user_id=user_id,
                provider=provider.provider_name,
                model_name=settings.OPENROUTER_TEXT_MODEL,
                prompt_version=prompt_version,
                input_type="voice" if is_voice else "text",
                raw_input=(
                    f"{text}\n\nAdditional context: {additional_context.strip()}"
                    if additional_context and additional_context.strip()
                    else text
                ),
                raw_output=str(raw_output) if raw_output else None,
                latency_ms=latency_ms,
                success=success,
                error_message=error_msg,
                token_usage=raw_result.token_usage if raw_result is not None else None,
            )

    async def estimate_from_image(
        self,
        image_url: str,
        user_context: UserContext | None = None,
        optional_hint: str | None = None,
        user_id: int | None = None,
        provider_override: str | None = None,
        additional_context: str | None = None,
    ) -> MealEstimateResult:
        provider = self._get_provider(provider_override)
        start_time = time.perf_counter()
        success = False
        raw_result = None
        raw_output = None
        error_msg = None

        try:
            raw_result = await provider.estimate_meal_from_image(
                image_url,
                user_context,
                optional_hint,
                additional_context=additional_context,
            )
            raw_output = raw_result.raw_output

            validated_result = AIValidationService.validate_and_normalize_estimate(raw_result)
            self._finalize(validated_result, user_context, "photo")
            success = True
            return validated_result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in estimate_from_image: {e}")
            raise e
        finally:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            prompt_version = (
                raw_result.prompt_version if raw_result is not None else IMAGE_MEAL_ESTIMATION_PROMPT_VERSION
            )
            await self.inference_logger.log_call(
                user_id=user_id,
                provider=provider.provider_name,
                model_name=settings.OPENROUTER_IMAGE_MODEL,
                prompt_version=prompt_version,
                input_type="photo",
                raw_input=(
                    f"Image URL: {image_url} | Hint: {optional_hint or 'None'} | "
                    f"Additional context: {additional_context or 'None'}"
                ),
                raw_output=str(raw_output) if raw_output else None,
                latency_ms=latency_ms,
                success=success,
                error_message=error_msg,
                token_usage=raw_result.token_usage if raw_result is not None else None,
            )

    async def estimate_from_voice(
        self,
        audio_url: str,
        user_context: UserContext | None = None,
        user_id: int | None = None,
        provider_override: str | None = None,
        additional_context: str | None = None,
    ) -> tuple[str, MealEstimateResult]:
        """Transcribes the voice input first, then runs the text calorie estimation pipeline."""
        # 1. Transcribe voice audio
        transcription_result = await self.speech_service.transcribe_audio(
            audio_url=audio_url,
            user_id=user_id,
            provider_override=provider_override,
        )
        transcript = transcription_result.transcript

        # 2. Estimate from the transcript. Cache may serve it without the second
        #    LLM call; the ASR flag stops the model dead-ending on disfluencies;
        #    the transcription confidence caps the final estimate confidence.
        estimation_result = await self.estimate_from_text(
            text=transcript,
            user_context=user_context,
            user_id=user_id,
            provider_override=provider_override,
            is_voice=True,
            channel="voice",
            transcription_confidence=transcription_result.confidence,
            additional_context=additional_context,
        )

        return transcript, estimation_result

    async def refine_estimate(
        self,
        meal_snapshot: dict,
        user_refinement: str,
        source_type: str,
        user_context: UserContext | None = None,
        user_id: int | None = None,
        provider_override: str | None = None,
    ) -> MealEstimateResult:
        provider = self._get_provider(provider_override)
        start_time = time.perf_counter()
        success = False
        raw_result = None
        raw_output = None
        error_msg = None

        try:
            raw_result = await provider.refine_meal_estimate(
                meal_snapshot,
                user_refinement,
                source_type,
                user_context,
            )
            raw_output = raw_result.raw_output
            validated_result = AIValidationService.validate_and_normalize_estimate(raw_result)
            validated_result.source_type = source_type  # type: ignore[assignment]
            score = AIConfidenceService.compute(validated_result)
            validated_result.confidence_score = score
            validated_result.confidence = bucket_confidence(score)  # type: ignore[assignment]
            validated_result.ai_summary = raw_result.ai_summary
            validated_result.changes_made = raw_result.changes_made
            success = True
            return validated_result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in refine_estimate: {e}")
            raise e
        finally:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            prompt_version = raw_result.prompt_version if raw_result is not None else MEAL_REFINEMENT_PROMPT_VERSION
            await self.inference_logger.log_call(
                user_id=user_id,
                provider=provider.provider_name,
                model_name=settings.OPENROUTER_TEXT_MODEL,
                prompt_version=prompt_version,
                input_type="meal_refinement",
                raw_input=f"Snapshot: {meal_snapshot}\n\nUser refinement: {user_refinement}",
                raw_output=str(raw_output) if raw_output else None,
                latency_ms=latency_ms,
                success=success,
                error_message=error_msg,
                token_usage=raw_result.token_usage if raw_result is not None else None,
            )

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
        raw_result = None
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
            prompt_version = (
                raw_result.prompt_version if raw_result is not None else MEAL_COMPLETION_PROMPT_VERSION
            )
            await self.inference_logger.log_call(
                user_id=user_id,
                provider=provider.provider_name,
                model_name=settings.OPENROUTER_TEXT_MODEL,
                prompt_version=prompt_version,
                input_type="meal_completion",
                raw_input=completion_req.model_dump_json(),
                raw_output=str(raw_output) if raw_output else None,
                latency_ms=latency_ms,
                success=success,
                error_message=error_msg,
                token_usage=raw_result.token_usage if raw_result is not None else None,
            )
