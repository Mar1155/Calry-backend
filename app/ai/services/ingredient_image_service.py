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
import re
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.density_table import lookup_food
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

_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")

# density_table buckets whose members can look visibly different enough that
# sharing one illustration risks reading as wrong rather than "close enough"
# (an apple and a banana; broccoli and a bell pepper). Excluded from bucket
# sharing here — these keep a per-name cache entry like any unmatched
# ingredient. Every other bucket (cheese, pasta, rice, oil, bread, legumes...)
# is visually generic enough that one illustration per bucket is a fair
# trade for a much higher cache-hit rate. Tune this set based on what looks
# right in the app, not on food-density accuracy — that concern belongs to
# density_table.py's own callers, not this one.
_NO_BUCKET_SHARING = frozenset({"Fruit", "Cooked vegetables"})


def _strip_parenthetical(name: str) -> str:
    """Drops a clarifying aside ("Formaggio Grattugiato (Parmigiano/Grana)" ->
    "Formaggio Grattugiato"): the model adds these to explain a guess, not to
    name a different ingredient, and left in they only fragment the cache."""
    stripped = _PARENTHETICAL_RE.sub(" ", name)
    return " ".join(stripped.split()) or name


def _cache_target(name: str) -> tuple[str, str]:
    """Returns (cache_key, name_to_generate_from) for `name`.

    Deliberately looser than the food-memory cache's plain
    canonicalize_food_name: an illustration only needs to look plausibly
    right, never calorie precision, so most real-world phrasings of the same
    rough ingredient should share one image instead of each paying for its
    own generation. Reuses density_table's curated, bilingual (English/
    Italian) keyword buckets — e.g. "Pasta Di Semola Cotta", "Spaghetti" and
    "Penne" all become the one "Cooked pasta" bucket — before falling back to
    the plain per-name canonical key for anything the table doesn't
    recognise, which keeps its own, unshared image.

    Only English and Italian are covered by density_table's keyword lists
    today, so the same real ingredient logged in Spanish, Chinese, Japanese
    or Arabic still gets its own cache entry rather than sharing across
    languages — the model's calorie estimate isn't affected either way, only
    how often the illustration is reused.
    """
    cleaned = _strip_parenthetical(name)
    bucket = lookup_food(cleaned)
    if bucket is not None and bucket.name not in _NO_BUCKET_SHARING:
        return canonicalize_food_name(bucket.name), bucket.name
    return canonicalize_food_name(cleaned), cleaned


def _asset_key(canonical: str, content_type: str) -> str:
    # sha1 of the canonical key, not the raw name: stable across languages/
    # unicode, filesystem- and S3-key-safe, and one name always maps to one
    # key regardless of how a particular meal spelled or capitalized it.
    #
    # Prefixed with uploads/, not a bare ingredients/ prefix: on this bucket,
    # object ACLs are disabled (S3_PUBLIC_READ=false everywhere — see
    # storage.py's _save_upload_s3 comment), so public read comes from an
    # account-level bucket policy scoped to uploads/*. Verified live on
    # 2026-09-30: a key outside that prefix uploads fine but 403s on fetch.
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
    ext = _EXTENSION_BY_CONTENT_TYPE.get(content_type, "png")
    return f"uploads/ingredients/{digest}.{ext}"


class IngredientImageService:
    def __init__(self, db: AsyncSession, provider: OpenRouterProvider | None = None):
        self.db = db
        self.provider = provider or OpenRouterProvider()
        self.inference_logger = AIInferenceLogger(db)

    async def _get_cached(self, canonical_key: str) -> IngredientImage | None:
        return await self.db.scalar(select(IngredientImage).where(IngredientImage.canonical_key == canonical_key))

    async def get_or_generate(self, name: str, *, user_id: int | None = None) -> str | None:
        """Returns an image URL for `name` — cached, freshly generated, or a
        stale cached fallback — or None when nothing usable exists yet.

        `name` is only ever used for the cache lookup and, when nothing
        matches a shared bucket, as the literal generation prompt subject —
        see _cache_target for how variant phrasings of the same ingredient
        end up sharing one cached image."""
        canonical, generation_name = _cache_target(name)
        if not canonical:
            return None

        existing = await self._get_cached(canonical)
        if existing is not None and existing.prompt_version == INGREDIENT_IMAGE_PROMPT_VERSION:
            return existing.image_url

        started = time.perf_counter()
        try:
            result = await self.provider.generate_ingredient_image(generation_name)
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
            raw_output=f"canonical_key={canonical} bucket={generation_name!r} url={uploaded['url']}",
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
            display_name=generation_name,
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
