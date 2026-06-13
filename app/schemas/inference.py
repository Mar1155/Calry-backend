import datetime as dt

from pydantic import BaseModel, ConfigDict


class AIInferenceLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None = None
    model_name: str
    input_type: str
    raw_input: str
    raw_output: str | None = None
    latency_ms: int
    success: bool
    created_at: dt.datetime
