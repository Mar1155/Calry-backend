import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class WeightEntryCreate(BaseModel):
    weight_kg: float = Field(..., ge=30.0, le=300.0)
    # Day the weigh-in belongs to; omitted means today. Past days allowed.
    date: dt.date | None = None


class WeightEntryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight_kg: float = Field(..., ge=30.0, le=300.0)


class WeightEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    weight_kg: float
    local_date: dt.date
    source: str
    created_at: dt.datetime
    # Smoothed weight on this day; filled in by the overview.
    trend_kg: float | None = None


class AdaptiveTargetResponse(BaseModel):
    status: str
    window_days: int
    weigh_ins: int
    weigh_in_span_days: int
    logged_days: int
    adherent_days: int
    required_weigh_ins: int
    required_span_days: int
    required_logged_days: int
    current_goal: int
    average_net_intake: int | None = None
    observed_maintenance: int | None = None
    expected_weekly_change_kg: float | None = None
    actual_weekly_change_kg: float | None = None
    recommended_goal: int | None = None
    change: int | None = None
    confidence: float | None = None
    capped_at_minimum: bool = False


class WeightOverviewResponse(BaseModel):
    entries: list[WeightEntryResponse]
    latest_kg: float | None = None
    trend_kg: float | None = None
    # Least-squares rate over the last 28 days, kg per week.
    weekly_change_kg: float | None = None
    # Trend now minus trend at the first entry in range.
    change_in_range_kg: float | None = None
    goal_type: str
    adaptive: AdaptiveTargetResponse


class AdaptiveTargetApply(BaseModel):
    # The goal the user saw; rejected if the suggestion changed meanwhile.
    recommended_goal: int = Field(..., ge=500, le=10000)
