"""Ingredient image generation (C29) result contract.

Not a JSON-parsed AI contract like the other schemas in this package — the
model's response here is raw image bytes, so a plain dataclass is enough.
"""

from dataclasses import dataclass


@dataclass
class IngredientImageResult:
    image_bytes: bytes
    content_type: str
    model_name: str
    prompt_version: str
    latency_ms: int
    token_usage: dict | None = None
