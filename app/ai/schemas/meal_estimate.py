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


class SpeechTranscriptionResult(BaseModel):
    transcript: str
    confidence: Literal["low", "medium", "high"] | None = None
    language: str | None = None
    model_name: str
    raw_output: dict | str | None = None
    latency_ms: int | None = None


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
