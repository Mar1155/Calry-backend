import datetime as dt
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class InsightCard(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=360)
    confidence: Literal["low", "medium", "high"]
    category: str = Field(min_length=1, max_length=50)
    metric: str = Field(min_length=1, max_length=120)
    evidence: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("title")
    @classmethod
    def title_has_at_most_six_words(cls, value: str) -> str:
        value = value.strip()
        if len(value.split()) > 6:
            raise ValueError("Insight title must contain at most six words.")
        return value


class WeeklyObservation(InsightCard):
    days_analyzed: int = Field(ge=1, le=7)
    explanation: str = Field(default="", max_length=240)


class WeeklyReportResponse(BaseModel):
    average_calories: int
    days_within_target: int
    highest_calories: int
    lowest_calories: int
    most_frequent_meal: str | None = None
    days_logged: int
    ai_observation: WeeklyObservation | None = None


class PatternInsightsResponse(BaseModel):
    patterns: list[InsightCard] = Field(default_factory=list, max_length=4)
    days_logged: int
    period_days: int = 30


class StoryEvidence(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=160)

    @field_validator("label")
    @classmethod
    def evidence_label_is_human_readable(cls, value: str) -> str:
        value = value.strip()
        if "_" in value and " " not in value:
            raise ValueError("Evidence labels cannot expose internal field names.")
        return value


class InsightStory(BaseModel):
    story_id: str = Field(min_length=16, max_length=64)
    detector_id: str = Field(min_length=1, max_length=80)
    pattern_key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=360)
    confidence_label: Literal["low", "medium", "high"]
    metric: str = Field(min_length=1, max_length=120)
    explanation: str = Field(default="", max_length=240)
    evidence: list[StoryEvidence] = Field(default_factory=list, max_length=6)
    category: Literal["accuracy", "consistency", "macros", "meals", "activity", "water", "progress"]
    direction: Literal["positive", "negative", "neutral"] = "neutral"

    @field_validator("title")
    @classmethod
    def story_title_has_at_most_six_words(cls, value: str) -> str:
        value = value.strip()
        if len(value.split()) > 6:
            raise ValueError("Insight title must contain at most six words.")
        return value

    @field_validator("message")
    @classmethod
    def story_message_has_at_most_two_sentences(cls, value: str) -> str:
        value = value.strip()
        if len([part for part in re.split(r"[.!?。！？]+", value) if part.strip()]) > 2:
            raise ValueError("Insight message must contain at most two sentences.")
        return value

    @field_validator("explanation")
    @classmethod
    def story_explanation_has_at_most_one_sentence(cls, value: str) -> str:
        value = value.strip()
        if len([part for part in re.split(r"[.!?。！？]+", value) if part.strip()]) > 1:
            raise ValueError("Insight explanation must contain at most one sentence.")
        return value


class InsightStoriesResponse(BaseModel):
    snapshot_id: str | None = None
    scope: str
    source_data_version: str
    status: Literal["fresh", "stale", "generating", "failed"]
    update_pending: bool = False
    generated_at: dt.datetime | None = None
    stories: list[InsightStory] = Field(default_factory=list, max_length=4)
    ranking_metadata: dict[str, Any] = Field(default_factory=dict)
