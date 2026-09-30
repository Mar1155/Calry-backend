"""Ingredient-name English-translation prompt (C29 cache normalization).

The illustration cache keys on an English name so the same real ingredient,
logged in any language, shares one cached image instead of paying for a
fresh generation per language. This is the fallback step for names
density_table's EN/IT keyword buckets don't already recognise (see
ingredient_image_service._resolve_cache_target).
"""

INGREDIENT_TRANSLATION_PROMPT_VERSION = "ingredient_translation_v1"

INGREDIENT_TRANSLATION_SYSTEM_PROMPT = (
    "You translate a single food ingredient name into English for a visual "
    'asset cache key. Respond with strict JSON: {"english_name": "..."}. Use '
    "the shortest common English name for the core ingredient (2-4 words "
    "max), singular, lowercase, no brand names, no articles, no extra "
    "commentary. If the input is already English, return it unchanged aside "
    "from this cleanup."
)


def build_ingredient_translation_user_text(name: str) -> str:
    return f"Ingredient name: {name}"
