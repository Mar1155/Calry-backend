from typing import Literal

from pydantic import BaseModel, Field


class MealEstimateItem(BaseModel):
    name: str
    quantity_estimate: str | None = None
    weight_grams: int | None = None
    calories_per_100g: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    estimated_calories: int = Field(default=0, ge=0)


class MealEstimateResult(BaseModel):
    meal_name: str
    estimated_calories: int = Field(..., ge=0)
    estimated_min_calories: int | None = None
    estimated_max_calories: int | None = None
    confidence: Literal["low", "medium", "high"]
    meal_category_suggestion: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    meal_category_confidence: Literal["low", "medium", "high"] | None = None
    source_type: Literal["text", "voice", "photo"]
    items: list[MealEstimateItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarifying_question: str | None = None
    model_name: str
    prompt_version: str
    raw_output: dict | str | None = None
    latency_ms: int | None = None
    total_protein_g: float | None = None
    total_carbs_g: float | None = None
    total_fat_g: float | None = None
    estimation_reasoning: str | None = None

    # Deterministic confidence (computed post-validation by AIConfidenceService).
    confidence_score: float | None = None

    # Internal validation flags — feed the confidence score; not part of the API
    # response contract (MealResponse does not expose them).
    density_clamped: bool = False
    macro_mismatch: bool = False
    total_realigned: bool = False
    degraded_extraction: bool = False

    # Raw provider token usage (prompt/completion/cached) for cost telemetry.
    token_usage: dict | None = None

    # Revision-only metadata. These fields are populated by the conversational
    # refinement pipeline and may be surfaced to the client before save.
    ai_summary: str | None = None
    changes_made: list[str] = Field(default_factory=list)


class SpeechTranscriptionResult(BaseModel):
    transcript: str
    confidence: Literal["low", "medium", "high"] | None = None
    language: str | None = None
    model_name: str
    raw_output: dict | str | None = None
    latency_ms: int | None = None
    token_usage: dict | None = None


class UserContext(BaseModel):
    daily_calorie_goal: int | None = None
    locale: str | None = None
    timezone: str | None = None
    previous_corrections_summary: str | None = None
    sex: str | None = None
    age: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    goal_type: str | None = None
    avg_correction_percent: float | None = None
    # Deterministic per-source-type correction multiplier fractions (C11), applied
    # post-estimation by the orchestrator — NOT rendered into any prompt.
    correction_bias_by_source: dict[str, float] | None = None


# LLM-facing response schema (C16). Request-only subset — excludes derived fields
# (item estimated_calories, estimation_reasoning) since validation computes them.
# Kept permissive (strict=false at the call site) because OpenRouter/Gemini
# structured output guarantees shape, not OpenAI-style strict value enforcement.
MEAL_ESTIMATE_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "meal_name": {"type": "string", "description": "Short user-facing name for the consumed meal."},
        "estimated_calories": {
            "type": "integer",
            "description": "Central kcal estimate; rounded sum of item weight times item kcal per 100 g.",
        },
        "estimated_min_calories": {
            "type": ["integer", "null"],
            "description": "Realistic lower kcal bound, or null only for a clarification response.",
        },
        "estimated_max_calories": {
            "type": ["integer", "null"],
            "description": "Realistic upper kcal bound, or null only for a clarification response.",
        },
        "meal_category_suggestion": {
            "type": ["string", "null"],
            "enum": ["breakfast", "lunch", "dinner", "snack", None],
            "description": "Organizational meal category when supported by context; otherwise null.",
        },
        "meal_category_confidence": {
            "type": ["string", "null"],
            "enum": ["low", "medium", "high", None],
            "description": "Evidence quality for the category suggestion; null when category is null.",
        },
        "items": {
            "type": "array",
            "minItems": 1,
            "description": "Each calorie-bearing component exactly once; no composite/component overlap.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Specific but non-invented food component.",
                    },
                    "quantity_estimate": {
                        "type": ["string", "null"],
                        "description": "Human-readable amount actually consumed, such as '1 medium bowl'.",
                    },
                    "weight_grams": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2147483647,
                        "description": "Estimated edible grams consumed, in the same cooked/dry state as density.",
                    },
                    "calories_per_100g": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 900,
                        "description": "Energy density in kcal per 100 g, not calories for the whole item.",
                    },
                    "protein_g": {
                        "type": ["number", "null"],
                        "description": "Protein grams in this full consumed item portion, not per 100 g.",
                    },
                    "carbs_g": {
                        "type": ["number", "null"],
                        "description": "Carbohydrate grams in this full consumed item portion, not per 100 g.",
                    },
                    "fat_g": {
                        "type": ["number", "null"],
                        "description": "Fat grams in this full consumed item portion, not per 100 g.",
                    },
                },
                "required": [
                    "name",
                    "quantity_estimate",
                    "weight_grams",
                    "calories_per_100g",
                    "protein_g",
                    "carbs_g",
                    "fat_g",
                ],
            },
        },
        "needs_clarification": {
            "type": "boolean",
            "description": "True only when no defensible food estimate can be made.",
        },
        "clarifying_question": {
            "type": ["string", "null"],
            "description": "One concise question when clarification is required; otherwise null.",
        },
    },
    "required": [
        "meal_name",
        "estimated_calories",
        "estimated_min_calories",
        "estimated_max_calories",
        "meal_category_suggestion",
        "meal_category_confidence",
        "items",
        "needs_clarification",
        "clarifying_question",
    ],
}


MEAL_REFINEMENT_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **MEAL_ESTIMATE_RESPONSE_SCHEMA["properties"],
        "ai_summary": {
            "type": ["string", "null"],
            "description": "One short sentence explaining the supported numerical revision.",
        },
        "changes_made": {
            "type": "array",
            "description": "Concise user-visible factual changes caused by the new evidence.",
            "items": {"type": "string"},
        },
    },
    "required": [*MEAL_ESTIMATE_RESPONSE_SCHEMA["required"], "ai_summary", "changes_made"],
}
