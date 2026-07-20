from typing import Literal

from pydantic import BaseModel, Field, field_validator


class InsightCard(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=360)
    confidence: Literal["low", "medium", "high"]
    category: str = Field(min_length=1, max_length=50)
    metric: str = Field(min_length=1, max_length=120)
    evidence: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("title")
    @classmethod
    def title_has_at_most_six_words(cls, value: str) -> str:
        value = value.strip()
        if len(value.split()) > 6:
            raise ValueError("Insight title must contain at most six words.")
        return value


class WeeklyObservation(InsightCard):
    days_analyzed: int = Field(ge=1, le=7)
    explanation: str = Field(default="", max_length=240)


class WeeklyReportResponse(BaseModel):
    average_calories: int
    days_within_target: int
    highest_calories: int
    lowest_calories: int
    most_frequent_meal: str | None = None
    days_logged: int
    ai_observation: WeeklyObservation | None = None


class PatternInsightsResponse(BaseModel):
    patterns: list[InsightCard] = Field(default_factory=list, max_length=4)
    days_logged: int
    period_days: int = 30
