"""Ingredient image prompt (C29).

One fixed illustration style, shared by every ingredient, so the thumbnails
across a meal — and across different meals and users — read as one coherent
set instead of a grab-bag of random photo styles. Only the ingredient name
varies between calls. Bump the version when the style changes; the cache
treats a stale version as a miss and regenerates.
"""

INGREDIENT_IMAGE_PROMPT_VERSION = "ingredient_image_v1"

# Matches Calry's warm-paper palette (DESIGN.md journal-paper) so a generated
# thumbnail sits naturally on the app's own background.
_STYLE = (
    "flat minimalist food illustration, a single isolated ingredient, "
    "centered and filling most of the frame, plain warm cream background "
    "(#FBF3E9), soft even lighting, no plate, no cutlery, no hands, no text, "
    "no watermark, no brand logos, only a soft contact shadow, simple clean "
    "shapes, warm appetizing color palette"
)


def build_ingredient_image_prompt(ingredient_name: str) -> str:
    name = " ".join(ingredient_name.strip().split())
    return f"A single serving of {name}. {_STYLE}."
