from typing import Literal

from pydantic import BaseModel, Field


class MealCompletionPreferences(BaseModel):
    meal_type: Literal["lunch", "dinner", "snack"] | None = None
    max_prep_minutes: int | None = Field(default=None, ge=5, le=120)
    dietary_preference: Literal["any", "vegetarian", "vegan"] = "any"
    available_ingredients: list[str] = Field(default_factory=list, max_length=12)


class MealSuggestionResponse(BaseModel):
    meal_name: str
    description: str
    estimated_calories: int
    protein_g: float
    carbs_g: float
    fat_g: float
    ingredients: list[str]
    preparation_hint: str
    reasoning: str
    meal_type: str
    difficulty: str
    prep_time_minutes: int


class MealCompletionResponse(BaseModel):
    suggestions: list[MealSuggestionResponse]
    daily_context_summary: str
    macro_balance_note: str
    remaining_calories: int
    consumed_calories: int
    daily_goal: int
