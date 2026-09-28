from typing import Literal

from pydantic import BaseModel, Field


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
    dietary_tags: list[Literal["vegetarian", "vegan"]] = Field(default_factory=list)


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
    target_protein_g: float | None = None
    target_carbs_g: float | None = None
    target_fat_g: float | None = None
    meals_eaten_today: list[str]
    requested_meal_type: Literal["lunch", "dinner", "snack"] | None = None
    max_prep_minutes: int | None = None
    dietary_preference: Literal["any", "vegetarian", "vegan"] = "any"
    available_ingredients: list[str] = Field(default_factory=list)
