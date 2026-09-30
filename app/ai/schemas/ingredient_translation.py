"""Ingredient-name English-translation (C29 cache normalization) result contract."""

from dataclasses import dataclass

INGREDIENT_TRANSLATION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "english_name": {"type": "string"},
    },
    "required": ["english_name"],
    "additionalProperties": False,
}


@dataclass
class IngredientTranslationResult:
    english_name: str
    model_name: str
    prompt_version: str
    latency_ms: int
    token_usage: dict | None = None
