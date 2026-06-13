from pydantic import BaseModel


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
