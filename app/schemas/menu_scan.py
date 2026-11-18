from typing import Literal

from pydantic import BaseModel, Field

from app.ai.schemas.menu_scan import MenuDishAngle


class MenuScanRequest(BaseModel):
    image_urls: list[str] = Field(..., min_length=1, max_length=3)
    user_note: str | None = Field(default=None, max_length=500)
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] | None = None


class MenuDishIngredientResponse(BaseModel):
    name: str
    quantity_estimate: str | None = None
    weight_grams: int
    calories_per_100g: float
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    estimated_calories: int


class MenuDishSuggestionResponse(BaseModel):
    angle: MenuDishAngle
    dish_name: str
    original_menu_name: str | None = None
    description: str
    estimated_calories: int
    protein_g: float
    carbs_g: float
    fat_g: float
    why_for_you_today: str
    practical_tip: str | None = None
    meal_category: Literal["breakfast", "lunch", "dinner", "snack"] = "lunch"
    items: list[MenuDishIngredientResponse]


class MenuScanResponse(BaseModel):
    suggestions: list[MenuDishSuggestionResponse]
    daily_context_summary: str
    remaining_calories: int
    consumed_calories: int
    daily_goal: int
