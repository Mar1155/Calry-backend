import datetime as dt
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class OnboardingEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    journey_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    event_name: Literal["step_viewed", "step_completed", "back_tapped", "demo_opened", "auth_requested", "completion_requested", "completion_failed", "offer_viewed", "plans_requested", "offer_skipped", "offer_closed"]
    step: Literal["welcome", "goal", "formula", "age", "height", "weight", "activity", "target", "account", "offer"] | None = None
    locale: Literal["en", "it", "es", "zh", "ja", "ar"]
    platform: Literal["ios", "android", "macos", "windows", "linux", "fuchsia", "web"]
    occurred_at: dt.datetime


class OnboardingEventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[OnboardingEventInput] = Field(min_length=1, max_length=20)
