import datetime as dt
from typing import Literal, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MealItemBase(BaseModel):
    name: str
    estimated_calories: int = Field(..., ge=0)
    quantity_estimate: str | None = Field(default=None, max_length=100)
    weight_grams: int | None = Field(default=None)
    protein_g: float | None = Field(default=None)
    carbs_g: float | None = Field(default=None)
    fat_g: float | None = Field(default=None)


class MealItemCreate(MealItemBase):
    pass


class MealItemResponse(MealItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meal_id: int
    created_at: dt.datetime


# Input schemas for different meal logging channels
class MealCreateText(BaseModel):
    text: str = Field(..., min_length=2, max_length=2000)


class MealCreatePhoto(BaseModel):
    image_url: str = Field(..., min_length=5, max_length=1024)
    text: str | None = Field(default=None, max_length=1000)


class MealCreateVoice(BaseModel):
    audio_url: str = Field(..., min_length=5, max_length=1024)
    # Optional pre-transcribed text if client performs on-device transcription
    text: str | None = Field(default=None, max_length=2000)


class MealUpdate(BaseModel):
    confirmed_calories: int | None = Field(default=None, ge=0)
    meal_name: str | None = Field(default=None, max_length=255)
    items: list[MealItemCreate] | None = Field(default=None)


class MealResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    source_type: Literal["text", "voice", "photo"]
    original_input: str
    image_url: str | None = None
    audio_url: str | None = None
    meal_name: str | None = None
    estimated_calories: int
    estimated_min_calories: int | None = None
    estimated_max_calories: int | None = None
    total_protein_g: float | None = None
    total_carbs_g: float | None = None
    total_fat_g: float | None = None
    estimation_reasoning: str | None = None
    confirmed_calories: int | None = None
    ai_confidence: Literal["low", "medium", "high"] | None = None
    needs_clarification: bool = False
    clarifying_question: str | None = None
    created_at: dt.datetime
    confirmed_at: dt.datetime | None = None
    items: list[MealItemResponse] = []

    @field_validator("ai_confidence", mode="before")
    @classmethod
    def validate_confidence(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            if v < 0.5:
                return "low"
            elif v < 0.8:
                return "medium"
            else:
                return "high"
        if isinstance(v, str):
            try:
                val = float(v)
                if val < 0.5:
                    return "low"
                elif val < 0.8:
                    return "medium"
                else:
                    return "high"
            except ValueError:
                pass
            
            v_lower = v.lower()
            if v_lower in ("low", "medium", "high"):
                return v_lower
        return None

