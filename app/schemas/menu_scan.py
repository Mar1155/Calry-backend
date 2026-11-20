from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.ai.schemas.menu_scan import MenuDishAngle


def _validate_menu_source(image_urls: list[str], menu_url: str | None) -> tuple[list[str], str | None]:
    cleaned_images = [u.strip() for u in image_urls if isinstance(u, str) and u.strip()]
    cleaned_url = menu_url.strip() if isinstance(menu_url, str) and menu_url.strip() else None
    if not cleaned_images and not cleaned_url:
        raise ValueError("Provide at least one menu photo or a digital menu URL.")
    return cleaned_images, cleaned_url


class MenuCategoriesRequest(BaseModel):
    image_urls: list[str] = Field(default_factory=list, max_length=3)
    menu_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _ensure_source(self) -> "MenuCategoriesRequest":
        self.image_urls, self.menu_url = _validate_menu_source(self.image_urls, self.menu_url)
        return self


class MenuCategoryResponse(BaseModel):
    id: str
    name: str
    dish_count: int | None = None
    sample_dishes: list[str] = Field(default_factory=list)


class MenuCategoriesResponse(BaseModel):
    restaurant_name: str | None = None
    categories: list[MenuCategoryResponse]


class MenuScanRequest(BaseModel):
    image_urls: list[str] = Field(default_factory=list, max_length=3)
    menu_url: str | None = Field(default=None, max_length=2048)
    selected_categories: list[str] = Field(default_factory=list, max_length=30)
    user_note: str | None = Field(default=None, max_length=500)
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] | None = None

    @model_validator(mode="after")
    def _ensure_source(self) -> "MenuScanRequest":
        self.image_urls, self.menu_url = _validate_menu_source(self.image_urls, self.menu_url)
        self.selected_categories = [
            c.strip() for c in self.selected_categories if isinstance(c, str) and c.strip()
        ]
        return self


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
