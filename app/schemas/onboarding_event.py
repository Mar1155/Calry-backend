import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OnboardingEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    journey_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    event_name: Literal[
        "step_viewed",
        "step_completed",
        "back_tapped",
        "demo_opened",
        "demo_adjusted",
        "validation_failed",
        "auth_viewed",
        "auth_requested",
        "auth_succeeded",
        "auth_cancelled",
        "auth_failed",
        "completion_requested",
        "completion_failed",
        "offer_viewed",
        "plans_requested",
        "offer_skipped",
        "offer_closed",
        "paywall_requested",
        "paywall_unavailable",
        "paywall_closed",
        "paywall_purchased",
        "paywall_restored",
        "paywall_not_presented",
        "app_first_frame",
        "welcome_ready",
        "first_input_submitted",
        "first_analysis_succeeded",
        "first_analysis_failed",
        "first_review_viewed",
        "first_correction_requested",
        "first_meal_save_failed",
    ]
    step: (
        Literal["welcome", "goal", "formula", "age", "height", "weight", "activity", "target", "account", "offer"]
        | None
    ) = None
    locale: Literal["en", "it", "es", "zh", "ja", "ar"]
    platform: Literal["ios", "android", "macos", "windows", "linux", "fuchsia", "web"]
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    onboarding_version: int | None = Field(default=None, ge=1, le=100)
    variant: str | None = Field(default=None, max_length=32)
    app_version: str | None = Field(default=None, max_length=32)
    duration_ms: int | None = Field(default=None, ge=0, le=600_000)
    occurred_at: dt.datetime


class OnboardingEventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[OnboardingEventInput] = Field(min_length=1, max_length=20)
