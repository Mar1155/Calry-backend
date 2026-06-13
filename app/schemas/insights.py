from pydantic import BaseModel


class WeeklyReportResponse(BaseModel):
    average_calories: int
    days_within_target: int
    highest_calories: int
    lowest_calories: int
    most_frequent_meal: str | None = None
    ai_observation: str


class PatternInsightsResponse(BaseModel):
    patterns: list[str]
