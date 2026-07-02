import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class DailySummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    date: dt.date
    consumed_calories: int
    burned_calories: int
    remaining_calories: int
    water_glasses: int


class WaterUpdateRequest(BaseModel):
    """One tap adds one glass; small negative deltas allow undoing mistaps."""

    delta: int = Field(default=1, ge=-5, le=5)
