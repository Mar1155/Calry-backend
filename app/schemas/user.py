import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    email: EmailStr
    name: str | None = None
    daily_calorie_goal: int = Field(default=2000, ge=500, le=10000)
    goal_type: Literal["lose", "maintain", "gain"] = "maintain"
    sex: Literal["male", "female"] | None = None
    age: int | None = Field(default=None, ge=1, le=120)
    height_cm: float | None = Field(default=None, ge=50.0, le=250.0)
    weight_kg: float | None = Field(default=None, ge=10.0, le=300.0)


class UserCreate(UserBase):
    firebase_uid: str


class UserUpdate(BaseModel):
    name: str | None = None
    daily_calorie_goal: int | None = Field(default=None, ge=500, le=10000)
    goal_type: Literal["lose", "maintain", "gain"] | None = None
    sex: Literal["male", "female"] | None = None
    age: int | None = Field(default=None, ge=1, le=120)
    height_cm: float | None = Field(default=None, ge=50.0, le=250.0)
    weight_kg: float | None = Field(default=None, ge=10.0, le=300.0)
    daily_protein_goal: int | None = None
    daily_carbs_goal: int | None = None
    daily_fat_goal: int | None = None


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    firebase_uid: str
    is_premium: bool
    premium_entitlement: str | None = None
    premium_expires_at: dt.datetime | None = None
    revenuecat_app_user_id: str | None = None
    daily_protein_goal: int | None = None
    daily_carbs_goal: int | None = None
    daily_fat_goal: int | None = None
    created_at: dt.datetime
    updated_at: dt.datetime | None = None
