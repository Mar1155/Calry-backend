from typing import Literal

from pydantic import BaseModel, Field


class MealReviewEvidence(BaseModel):
    label: str
    value: str


class MealOccasionReviewResponse(BaseModel):
    category: Literal["breakfast", "lunch", "dinner", "snack"]
    meal_ids: list[int]
    fingerprint: str
    meal_count: int = Field(ge=1)
    total_calories: int = Field(ge=0)
    protein_g: float | None = Field(default=None, ge=0)
    carbs_g: float | None = Field(default=None, ge=0)
    fat_g: float | None = Field(default=None, ge=0)
    headline: str
    balance_note: str
    suggestion: str | None = None
    tags: list[str] = Field(default_factory=list)
    coverage_note: str
    evidence: list[MealReviewEvidence]


class DayReviewMeal(BaseModel):
    name: str
    category: Literal["breakfast", "lunch", "dinner", "snack"]
    calories: int = Field(ge=0)


class DayReviewResponse(BaseModel):
    date: str
    fingerprint: str
    meal_count: int = Field(ge=0)
    consumed_calories: int = Field(ge=0)
    daily_goal: int = Field(ge=0)
    remaining_calories: int
    protein_g: float | None = Field(default=None, ge=0)
    carbs_g: float | None = Field(default=None, ge=0)
    fat_g: float | None = Field(default=None, ge=0)
    headline: str
    standout: str
    balance_note: str
    tomorrow_tip: str | None = None
    coverage_note: str
    meals: list[DayReviewMeal]
