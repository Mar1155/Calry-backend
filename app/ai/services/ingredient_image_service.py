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

from app.ai.density_table import matching_foods
from app.ai.prompts.ingredient_image import INGREDIENT_IMAGE_PROMPT_VERSION
from app.ai.prompts.ingredient_translation import INGREDIENT_TRANSLATION_PROMPT_VERSION
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.services.inference_logger import AIInferenceLogger
from app.core.config import settings
from app.core.text_normalization import canonicalize_food_name
from app.models.ingredient_image import IngredientImage
from app.models.ingredient_translation import IngredientTranslation
from app.services.storage import save_generated_asset

logger = logging.getLogger("app.ai.ingredient_image")

# Logged alongside the other AI calls (photo_detection, voice_transcription, ...)
# so ingredient-image generations are visible in the same admin scan audit.
INGREDIENT_IMAGE_INPUT_TYPE = "ingredient_image"
INGREDIENT_TRANSLATION_INPUT_TYPE = "ingredient_translation"

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


# Words that end an ingredient name's head noun and start a modifier: in
# "acciughe all'olio d'oliva" or "anchovies in olive oil" the food is the
# anchovy, and the oil is only how it is preserved. Matched on folded tokens,
# so "all'olio" is already split into "all" + "olio".
_HEAD_CONNECTORS = frozenset({
    "in", "of", "with", "on", "and", "e", "ed", "con", "senza", "without",
    "a", "al", "all", "allo", "alla", "ai", "agli", "alle",
    "di", "d", "del", "dello", "della", "dei", "degli", "delle",
    "su", "sul", "sullo", "sulla", "sott", "sotto",
})
_NON_WORD_RE = re.compile(r"[^\w]+", flags=re.UNICODE)


def _head_phrase(name: str) -> str:
    """The part of an ingredient name before its first connector word
    ("Acciughe All'Olio D'Oliva" -> "acciughe", "Olio Extravergine Di Oliva"
    -> "olio extravergine"): the words that say *which* food it is, not
    what it comes in or with."""
    head: list[str] = []
    for token in _NON_WORD_RE.sub(" ", name.casefold()).split():
        if token in _HEAD_CONNECTORS:
            if head:
                break
            continue
        head.append(token)
    return " ".join(head)


def _shared_bucket(name: str) -> str | None:
    """The density_table bucket this ingredient can share an illustration
    with, or None. Only the name's head counts, and a head that touches more
    than one bucket ("tonno olio") is ambiguous, so it gets its own image
    rather than the wrong one."""
    buckets = matching_foods(_head_phrase(name))
    if len(buckets) != 1 or buckets[0].name in _NO_BUCKET_SHARING:
        return None
    return buckets[0].name


def _strip_parenthetical(name: str) -> str:
    """Drops a clarifying aside ("Formaggio Grattugiato (Parmigiano/Grana)" ->
    "Formaggio Grattugiato"): the model adds these to explain a guess, not to
    name a different ingredient, and left in they only fragment the cache."""
    stripped = _PARENTHETICAL_RE.sub(" ", name)
    return " ".join(stripped.split()) or name

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

    async def _get_cached_translation(self, source_key: str) -> IngredientTranslation | None:
        return await self.db.scalar(
            select(IngredientTranslation).where(IngredientTranslation.source_key == source_key)
        )

    async def _resolve_cache_target(self, name: str, *, user_id: int | None) -> tuple[str, str]:
        """Returns (cache_key, name_to_generate_from) for `name` — always an
        English name, so the same real ingredient shares one cached image
        regardless of what language it was logged in.

        Deliberately looser than the food-memory cache's plain
        canonicalize_food_name: an illustration only needs to look plausibly
        right, never calorie precision, so most real-world phrasings of the
        same rough ingredient should share one image instead of each paying
        for its own generation.

        Two layers, cheapest first:
        1. density_table's curated, bilingual (English/Italian) keyword
           buckets — e.g. "Pasta Di Semola Cotta", "Spaghetti" and "Penne"
           all become the one "Cooked pasta" bucket — free, no network call.
           Matched on the name's head only (see _shared_bucket), so
           "acciughe all'olio d'oliva" never borrows the olive-oil image.
        2. For anything that table doesn't recognise (any other language, or
           an EN/IT name outside its buckets), a best-effort model
           translation to English, memoized in ingredient_translations so
           the same source name is only ever translated once, ever. A
           translation failure falls back to the plain per-name canonical
           key of the original (untranslated) name, which still caches —
           just not shared across languages until it succeeds later.
        """
        cleaned = _strip_parenthetical(name)
        if not cleaned.strip():
            return "", ""

        bucket = _shared_bucket(cleaned)
        if bucket is not None:
            return canonicalize_food_name(bucket), bucket

        source_key = canonicalize_food_name(cleaned)
        cached_translation = await self._get_cached_translation(source_key)
        if cached_translation is not None:
            return canonicalize_food_name(cached_translation.english_name), cached_translation.english_name

        started = time.perf_counter()
        try:
            result = await self.provider.translate_ingredient_name(cleaned)
        except Exception as exc:  # noqa: BLE001 — best-effort; keep the original name on any failure
            logger.warning(
                "event=ingredient_translation_failed name=%s error_type=%s error=%s",
                cleaned,
                type(exc).__name__,
                exc,
            )
            await self.inference_logger.log_call(
                user_id=user_id,
                provider="openrouter",
                model_name=settings.OPENROUTER_DETECTION_MODEL,
                prompt_version=INGREDIENT_TRANSLATION_PROMPT_VERSION,
                input_type=INGREDIENT_TRANSLATION_INPUT_TYPE,
                raw_input=cleaned,
                raw_output=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                success=False,
                error_message=str(exc),
            )
            return source_key, cleaned

        await self.inference_logger.log_call(
            user_id=user_id,
            provider="openrouter",
            model_name=result.model_name,
            prompt_version=result.prompt_version,
            input_type=INGREDIENT_TRANSLATION_INPUT_TYPE,
            raw_input=cleaned,
            raw_output=result.english_name,
            latency_ms=result.latency_ms,
            success=True,
            token_usage=result.token_usage,
        )

        entry = IngredientTranslation(
            source_key=source_key,
            source_name=cleaned,
            english_name=result.english_name,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
        )
        try:
            # A savepoint, not a plain flush: a concurrent worker may have
            # memoized the same source_key first — see the matching comment
            # on the IngredientImage insert below for why this must not roll
            # back the caller's whole session.
            async with self.db.begin_nested():
                self.db.add(entry)
                await self.db.flush()
        except IntegrityError:
            winner = await self._get_cached_translation(source_key)
            english_name = winner.english_name if winner is not None else result.english_name
            return canonicalize_food_name(english_name), english_name
        return canonicalize_food_name(result.english_name), result.english_name

    async def get_or_generate(self, name: str, *, user_id: int | None = None) -> str | None:
        """Returns an image URL for `name` — cached, freshly generated, or a
        stale cached fallback — or None when nothing usable exists yet.

        `name` is only ever used for the cache lookup and, as a translated or
        bucketed English name, the literal generation prompt subject — see
        _resolve_cache_target for how variant phrasings of the same
        ingredient, in any language, end up sharing one cached image."""
        canonical, generation_name = await self._resolve_cache_target(name, user_id=user_id)
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
