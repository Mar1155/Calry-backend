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
    "properties": {
        "meal_name": {"type": "string"},
        "estimated_calories": {"type": "integer"},
        "estimated_min_calories": {"type": ["integer", "null"]},
        "estimated_max_calories": {"type": ["integer", "null"]},
        "total_protein_g": {"type": ["number", "null"]},
        "total_carbs_g": {"type": ["number", "null"]},
        "total_fat_g": {"type": ["number", "null"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity_estimate": {"type": ["string", "null"]},
                    "weight_grams": {"type": ["integer", "null"]},
                    "calories_per_100g": {"type": ["number", "null"]},
                    "protein_g": {"type": ["number", "null"]},
                    "carbs_g": {"type": ["number", "null"]},
                    "fat_g": {"type": ["number", "null"]},
                },
                "required": ["name", "weight_grams", "calories_per_100g"],
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "needs_clarification": {"type": "boolean"},
        "clarifying_question": {"type": ["string", "null"]},
    },
    "required": ["meal_name", "estimated_calories", "confidence", "items"],
}


MEAL_REFINEMENT_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        **MEAL_ESTIMATE_RESPONSE_SCHEMA["properties"],
        "ai_summary": {"type": ["string", "null"]},
        "changes_made": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "meal_name",
        "estimated_calories",
        "confidence",
        "items",
        "ai_summary",
        "changes_made",
    ],
}
