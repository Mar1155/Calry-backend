"""Food region detection orchestration (C28).

Runs the cheap detection call *in parallel* with the photo estimate. The
estimate's prompt, model and numbers are untouched: detection only adds pins.

Concurrency contract: ``detect`` never touches the database — the photo worker
runs it as an asyncio task next to the estimator, which owns the session.
Logging happens afterwards, sequentially, through ``log``.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.food_detection import FOOD_DETECTION_PROMPT_VERSION
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.food_detection import DetectedRegion, FoodDetectionResult
from app.ai.services.inference_logger import AIInferenceLogger
from app.ai.streaming import protocol
from app.core.config import settings

logger = logging.getLogger("app.ai.food_detection")

DETECTION_INPUT_TYPE = "photo_detection"


@dataclass
class DetectionOutcome:
    image_url: str
    result: FoodDetectionResult | None = None
    error: str | None = None
    latency_ms: int = 0

    @property
    def regions(self) -> list[DetectedRegion]:
        return self.result.regions if self.result else []

    def regions_payload(self) -> list[dict]:
        return [region.model_dump() for region in self.regions]


class FoodDetectionService:
    def __init__(self, provider: OpenRouterProvider | None = None):
        self.provider = provider or OpenRouterProvider()

    async def detect(self, image_url: str, locale: str | None = None) -> DetectionOutcome:
        """Best-effort detection. Never raises (except cancellation)."""
        started = time.perf_counter()
        try:
            result = await self.provider.detect_food_regions(image_url, locale)
            outcome = DetectionOutcome(image_url=image_url, result=result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — pins are optional; never fail the meal
            logger.warning("event=food_detection_failed error_type=%s error=%s", type(exc).__name__, exc)
            outcome = DetectionOutcome(image_url=image_url, error=f"{type(exc).__name__}: {exc}")
        outcome.latency_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    async def detect_and_publish(self, job_id: str, image_url: str, locale: str | None = None) -> DetectionOutcome:
        """Detect, then push a `regions` event to the photo stream as soon as
        the boxes exist — usually before the first estimated item arrives."""
        from app.services import stream_bus

        outcome = await self.detect(image_url, locale)
        if outcome.regions:
            await stream_bus.publish(job_id, protocol.regions(outcome.regions_payload()))
        logger.info(
            "event=food_detection_completed job_id=%s region_count=%s latency_ms=%s failed=%s",
            job_id,
            len(outcome.regions),
            outcome.latency_ms,
            outcome.error is not None,
        )
        return outcome

    @staticmethod
    async def join(task: "asyncio.Task[DetectionOutcome] | None", grace_seconds: float) -> DetectionOutcome | None:
        """Wait briefly for a still-running detection once the estimate is
        ready; give up (and cancel it) rather than delay the meal."""
        if task is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, grace_seconds))
        except TimeoutError:
            task.cancel()
            logger.info("event=food_detection_abandoned reason=grace_elapsed grace_s=%s", grace_seconds)
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=food_detection_join_failed error=%s", exc)
            return None

    @staticmethod
    async def log(db: AsyncSession, user_id: int | None, outcome: DetectionOutcome) -> int | None:
        result = outcome.result
        return await AIInferenceLogger(db).log_call(
            user_id=user_id,
            provider="openrouter",
            model_name=result.model_name if result else settings.OPENROUTER_DETECTION_MODEL,
            prompt_version=result.prompt_version if result else FOOD_DETECTION_PROMPT_VERSION,
            input_type=DETECTION_INPUT_TYPE,
            raw_input=f"Image URL: {outcome.image_url}",
            raw_output=result.raw_output if result else None,
            latency_ms=result.latency_ms if result and result.latency_ms is not None else outcome.latency_ms,
            success=result is not None,
            error_message=outcome.error,
            token_usage=result.token_usage if result else None,
            finish_reason=result.finish_reason if result else None,
            detection=result,
        )
