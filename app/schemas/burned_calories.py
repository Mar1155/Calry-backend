import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class BurnedCaloriesCreate(BaseModel):
    activity_name: str | None = Field(default=None, max_length=255)
    calories: int = Field(..., ge=1, le=10000)


class BurnedCaloriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    activity_name: str | None = None
    calories: int
    created_at: dt.datetime
