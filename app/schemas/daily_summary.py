import datetime as dt

from pydantic import BaseModel, ConfigDict


class DailySummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    date: dt.date
    consumed_calories: int
    burned_calories: int
    remaining_calories: int
