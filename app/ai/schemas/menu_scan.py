from typing import Literal

from pydantic import BaseModel, Field

from app.ai.schemas.meal_estimate import MealEstimateItem

MenuDishAngle = Literal["best_fit", "for_you", "mindful_pleasure"]


class MenuDishSuggestionItem(BaseModel):
    angle: MenuDishAngle
    dish_name: str
    original_menu_name: str | None = None
    description: str
    estimated_calories: int = Field(ge=0)
    protein_g: float = Field(default=0.0, ge=0.0)
    carbs_g: float = Field(default=0.0, ge=0.0)
    fat_g: float = Field(default=0.0, ge=0.0)
    why_for_you_today: str
    practical_tip: str | None = None
    meal_category: Literal["breakfast", "lunch", "dinner", "snack"] = "lunch"
    items: list[MealEstimateItem] = Field(default_factory=list)


class MenuScanAIResult(BaseModel):
    is_menu: bool = True
    suggestions: list[MenuDishSuggestionItem] = Field(default_factory=list)
    daily_context_summary: str = ""
    model_name: str
    prompt_version: str
    raw_output: dict | str | None = None
    latency_ms: int | None = None
    token_usage: dict | None = None


class MenuScanAIRequest(BaseModel):
    image_urls: list[str] = Field(..., min_length=1, max_length=3)
    user_note: str | None = None
    requested_meal_type: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    remaining_calories: int
    consumed_calories: int
    daily_goal: int
    consumed_protein_g: float
    consumed_carbs_g: float
    consumed_fat_g: float
    target_protein_g: float | None = None
    target_carbs_g: float | None = None
    target_fat_g: float | None = None
    meals_eaten_today: list[str] = Field(default_factory=list)
    favorite_and_regular_foods: list[str] = Field(default_factory=list)
