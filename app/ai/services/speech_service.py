import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.voice_transcription import VOICE_TRANSCRIPTION_PROMPT_VERSION
from app.ai.providers.base import BaseAIProvider
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.meal_estimate import SpeechTranscriptionResult
from app.ai.services.inference_logger import AIInferenceLogger

logger = logging.getLogger("app.ai.speech_service")


class AISpeechService:
    """Service responsible for transcribing voice notes and audio URLs using OpenRouter."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.inference_logger = AIInferenceLogger(db)

        # Register OpenRouter as the provider
        self.providers: dict[str, BaseAIProvider] = {
            "openrouter": OpenRouterProvider(),
        }

    def _get_provider(self, provider_override: str | None = None) -> BaseAIProvider:
        # OpenRouter is the sole provider, so override is ignored or falls back to openrouter
        return self.providers["openrouter"]

    async def transcribe_audio(
        self,
        audio_url: str,
        user_id: int | None = None,
        provider_override: str | None = None,
    ) -> SpeechTranscriptionResult:
        provider = self._get_provider(provider_override)
        start_time = time.perf_counter()
        success = False
        raw_output = None
        error_msg = None

        try:
            result = await provider.transcribe_audio(audio_url)
            raw_output = result.raw_output
            success = True
            return result
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error during audio transcription: {e}")
            raise e
        finally:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            await self.inference_logger.log_call(
                user_id=user_id,
                provider=provider.provider_name,
                model_name=provider.provider_name + "-audio",
                prompt_version=VOICE_TRANSCRIPTION_PROMPT_VERSION,
                input_type="voice_transcription",
                raw_input=f"Audio URL: {audio_url}",
                raw_output=str(raw_output) if raw_output else None,
                latency_ms=latency_ms,
                success=success,
                error_message=error_msg,
            )
