"""Ingredient image generation and cache (C29).

One illustration per canonical ingredient name, shared across every user and
meal — "mozzarella" is generated once, ever, and every later meal with
mozzarella just reuses the stored URL. Generation is best-effort: nothing
here raises into the caller. A failure means an item's image_url stays null
(or falls back to a stale cached image); a later meal with the same
ingredient tries again.
"""

import hashlib
import logging
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.ingredient_image import INGREDIENT_IMAGE_PROMPT_VERSION
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.services.inference_logger import AIInferenceLogger
from app.core.config import settings
from app.core.text_normalization import canonicalize_food_name
from app.models.ingredient_image import IngredientImage
from app.services.storage import save_generated_asset

logger = logging.getLogger("app.ai.ingredient_image")

# Logged alongside the other AI calls (photo_detection, voice_transcription, ...)
# so ingredient-image generations are visible in the same admin scan audit.
INGREDIENT_IMAGE_INPUT_TYPE = "ingredient_image"

_EXTENSION_BY_CONTENT_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


def _asset_key(canonical: str, content_type: str) -> str:
    # sha1 of the canonical key, not the raw name: stable across languages/
    # unicode, filesystem- and S3-key-safe, and one name always maps to one
    # key regardless of how a particular meal spelled or capitalized it.
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
    ext = _EXTENSION_BY_CONTENT_TYPE.get(content_type, "png")
    return f"ingredients/{digest}.{ext}"


class IngredientImageService:
    def __init__(self, db: AsyncSession, provider: OpenRouterProvider | None = None):
        self.db = db
        self.provider = provider or OpenRouterProvider()
        self.inference_logger = AIInferenceLogger(db)

    async def _get_cached(self, canonical_key: str) -> IngredientImage | None:
        return await self.db.scalar(select(IngredientImage).where(IngredientImage.canonical_key == canonical_key))

    async def get_or_generate(self, name: str, *, user_id: int | None = None) -> str | None:
        """Returns an image URL for `name` — cached, freshly generated, or a
        stale cached fallback — or None when nothing usable exists yet."""
        canonical = canonicalize_food_name(name)
        if not canonical:
            return None

        existing = await self._get_cached(canonical)
        if existing is not None and existing.prompt_version == INGREDIENT_IMAGE_PROMPT_VERSION:
            return existing.image_url

        started = time.perf_counter()
        try:
            result = await self.provider.generate_ingredient_image(name)
        except Exception as exc:  # noqa: BLE001 — best-effort; a bad model day never blocks a meal
            logger.warning(
                "event=ingredient_image_generation_failed canonical_key=%s error_type=%s error=%s",
                canonical,
                type(exc).__name__,
                exc,
            )
            await self.inference_logger.log_call(
                user_id=user_id,
                provider="openrouter",
                model_name=settings.OPENROUTER_INGREDIENT_IMAGE_MODEL,
                prompt_version=INGREDIENT_IMAGE_PROMPT_VERSION,
                input_type=INGREDIENT_IMAGE_INPUT_TYPE,
                raw_input=name,
                raw_output=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                success=False,
                error_message=str(exc),
            )
            return existing.image_url if existing is not None else None

        key = _asset_key(canonical, result.content_type)
        try:
            uploaded = await save_generated_asset(result.image_bytes, result.content_type, key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=ingredient_image_upload_failed canonical_key=%s error_type=%s error=%s",
                canonical,
                type(exc).__name__,
                exc,
            )
            return existing.image_url if existing is not None else None

        await self.inference_logger.log_call(
            user_id=user_id,
            provider="openrouter",
            model_name=result.model_name,
            prompt_version=result.prompt_version,
            input_type=INGREDIENT_IMAGE_INPUT_TYPE,
            raw_input=name,
            raw_output=f"canonical_key={canonical} url={uploaded['url']}",
            latency_ms=result.latency_ms,
            success=True,
            token_usage=result.token_usage,
        )

        if existing is not None:
            existing.image_url = uploaded["url"]
            existing.storage_key = uploaded.get("key")
            existing.model_name = result.model_name
            existing.prompt_version = result.prompt_version
            await self.db.flush()
            return existing.image_url

        entry = IngredientImage(
            canonical_key=canonical,
            display_name=name.strip(),
            image_url=uploaded["url"],
            storage_key=uploaded.get("key"),
            model_name=result.model_name,
            prompt_version=result.prompt_version,
        )
        try:
            # A savepoint, not a plain flush: a unique-constraint failure here
            # (another worker inserted the same canonical_key concurrently)
            # must not roll back the caller's whole session — the meal this
            # generation was requested for may have other, already-flushed
            # item updates pending in the same transaction.
            async with self.db.begin_nested():
                self.db.add(entry)
                await self.db.flush()
        except IntegrityError:
            # The upload above wasn't wasted on nothing — it's just not the
            # copy we keep; use the row that won the race.
            winner = await self._get_cached(canonical)
            return winner.image_url if winner is not None else uploaded["url"]
        return entry.image_url
