from typing import Literal

from pydantic import BaseModel


class MealSuggestionItem(BaseModel):
    meal_name: str
    description: str
    estimated_calories: int
    protein_g: float
    carbs_g: float
    fat_g: float
    ingredients: list[str]
    preparation_hint: str
    reasoning: str
    meal_type: Literal["lunch", "dinner", "snack"]
    difficulty: Literal["easy", "medium"]
    prep_time_minutes: int


class MealCompletionResult(BaseModel):
    suggestions: list[MealSuggestionItem]
    daily_context_summary: str
    macro_balance_note: str
    model_name: str
    prompt_version: str
    raw_output: dict | str | None = None
    latency_ms: int | None = None
    token_usage: dict | None = None


class MealCompletionRequest(BaseModel):
    remaining_calories: int
    consumed_calories: int
    daily_goal: int
    consumed_protein_g: float
    consumed_carbs_g: float
    consumed_fat_g: float
    meals_eaten_today: list[str]
