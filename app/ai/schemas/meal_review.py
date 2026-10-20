from pydantic import BaseModel, Field


class MealOccasionReviewRequest(BaseModel):
    """Deterministic, already-aggregated facts about one meal occasion today, for the AI to reflect on."""

    category: str
    meal_names: list[str]
    total_calories: int
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    target_protein_g: float | None = None
    target_carbs_g: float | None = None
    target_fat_g: float | None = None
    remaining_calories: int | None = None
    daily_goal: int | None = None


class MealOccasionReviewResult(BaseModel):
    headline: str
    balance_note: str
    suggestion: str | None = None
    tags: list[str] = Field(default_factory=list)
    model_name: str
    prompt_version: str
    raw_output: dict | str | None = None
    latency_ms: int | None = None
    token_usage: dict | None = None


class DayReviewMealFact(BaseModel):
    name: str
    category: str
    calories: int


class DayReviewRequest(BaseModel):
    """Deterministic, already-aggregated facts about today, for the AI to reflect on."""

    meals: list[DayReviewMealFact]
    consumed_calories: int
    daily_goal: int
    remaining_calories: int
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    target_protein_g: float | None = None
    target_carbs_g: float | None = None
    target_fat_g: float | None = None


class DayReviewResult(BaseModel):
    headline: str
    standout: str
    balance_note: str
    tomorrow_tip: str | None = None
    model_name: str
    prompt_version: str
    raw_output: dict | str | None = None
    latency_ms: int | None = None
    token_usage: dict | None = None


__all__ = [
    "DayReviewMealFact",
    "DayReviewRequest",
    "DayReviewResult",
    "MealOccasionReviewRequest",
    "MealOccasionReviewResult",
]
