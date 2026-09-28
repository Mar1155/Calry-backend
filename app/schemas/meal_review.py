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
    observation: str
    suggestion: str | None = None
    coverage_note: str
    evidence: list[MealReviewEvidence]
