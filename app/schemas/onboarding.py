import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

GoalType = Literal["lose", "maintain", "gain"]
FormulaProfile = Literal["male", "female"]
ActivityLevel = Literal["low", "light", "moderate", "high"]
TargetPace = Literal["gradual", "balanced", "stronger"]
UnitSystem = Literal["metric", "imperial"]


class OnboardingInput(BaseModel):
    goal_type: GoalType
    formula_profile: FormulaProfile
    age: int = Field(ge=18, le=100)
    height_cm: float = Field(ge=120, le=230)
    weight_kg: float = Field(ge=30, le=300)
    activity_level: ActivityLevel
    target_pace: TargetPace | None = None
    preferred_unit_system: UnitSystem = "metric"


class CalculateTargetRequest(OnboardingInput):
    pass


class CompleteOnboardingRequest(OnboardingInput):
    owner_uid: str | None = Field(default=None, max_length=128)
    journey_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    started_at: dt.datetime | None = None
    selected_target: int = Field(ge=500, le=10000)
    target_was_manually_adjusted: bool = False
    unsafe_target_confirmed: bool = False
    onboarding_version: int = Field(default=2, ge=1)


class OnboardingStatusResponse(BaseModel):
    status: str
    current_step: str | None
    version: int
    completed_at: dt.datetime | None
