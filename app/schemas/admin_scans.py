import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.scan_review import SCAN_REVIEW_VERDICTS

ScanChannel = Literal["text", "voice", "photo"]
ScanKind = Literal["estimate", "refinement", "transcription", "all"]
ScanStatusFilter = Literal[
    "ok",
    "failed",
    "truncated",
    "degraded",
    "clarification",
    "corrected",
    "cache",
    "single_item",
    "unreviewed",
    "flagged",
]
ScanVerdict = Literal[SCAN_REVIEW_VERDICTS]  # type: ignore[valid-type]


class ScanReviewResponse(BaseModel):
    verdict: str
    expected_calories: int | None
    note: str | None
    reviewed_by_admin_uid: str
    updated_at: dt.datetime


class ScanListItem(BaseModel):
    id: int
    created_at: dt.datetime
    user_id: int | None
    input_type: str
    channel: str | None
    kind: str
    provider: str
    model_name: str
    prompt_version: str
    success: bool
    error_message: str | None
    finish_reason: str | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    item_count: int | None
    estimated_calories: int | None
    confidence_score: float | None
    degraded_extraction: bool | None
    needs_clarification: bool | None
    meal_id: int | None
    meal_name: str | None
    confirmed_calories: int | None
    correction_percent: float | None
    input_preview: str | None
    flags: list[str]
    review_verdict: str | None


class ScanListResponse(BaseModel):
    results: list[ScanListItem]
    total: int
    limit: int
    offset: int


class ScanRate(BaseModel):
    count: int
    rate: float | None


class ScanChannelSummary(BaseModel):
    channel: str
    total: int
    failed: ScanRate
    truncated: ScanRate
    degraded: ScanRate
    clarification: ScanRate
    cache_hits: ScanRate
    single_item: ScanRate
    median_latency_ms: int | None
    p90_latency_ms: int | None
    avg_completion_tokens: int | None
    confirmed: int
    median_abs_correction_percent: float | None
    within_10_percent: ScanRate


class ScanVersionSummary(BaseModel):
    model_name: str
    prompt_version: str
    total: int
    failure_rate: float | None
    degraded_rate: float | None
    truncation_rate: float | None
    median_latency_ms: int | None
    median_abs_correction_percent: float | None
    flagged_reviews: int
    reviewed: int


class ScanDailyPoint(BaseModel):
    date: dt.date
    total: int
    failed: int
    degraded: int
    truncated: int


class ScanSummaryResponse(BaseModel):
    days: int
    starts_at: dt.datetime
    generated_at: dt.datetime
    overall: ScanChannelSummary
    channels: list[ScanChannelSummary]
    versions: list[ScanVersionSummary]
    daily: list[ScanDailyPoint]
    verdicts: dict[str, int]
    reviewed: int
    # True when the window held more rows than the summary scans; rates then
    # describe the most recent `row_limit` scans.
    truncated_window: bool
    row_limit: int


class ScanMealItem(BaseModel):
    name: str
    quantity_estimate: str | None
    weight_grams: int
    calories_per_100g: float
    estimated_calories: int
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None


class ScanMeal(BaseModel):
    id: int
    source_type: str
    meal_name: str | None
    original_input: str
    meal_category: str
    estimated_calories: int
    estimated_min_calories: int | None
    estimated_max_calories: int | None
    confirmed_calories: int | None
    correction_percent: float | None
    total_protein_g: float | None
    total_carbs_g: float | None
    total_fat_g: float | None
    ai_confidence: str | None
    confidence_score: float | None
    needs_clarification: bool
    clarifying_question: str | None
    has_image: bool
    has_audio: bool
    created_at: dt.datetime
    confirmed_at: dt.datetime | None
    items: list[ScanMealItem]


class ScanRevision(BaseModel):
    id: int
    refinement_type: str
    user_input: str
    previous_calories: int
    revised_calories: int
    calorie_delta: int
    ai_summary: str | None
    model_name: str | None
    prompt_version: str | None
    created_at: dt.datetime


class ScanRelatedLog(BaseModel):
    id: int
    input_type: str
    created_at: dt.datetime
    success: bool
    model_name: str
    latency_ms: int
    raw_output_preview: str | None


class ScanDetailResponse(BaseModel):
    scan: ScanListItem
    raw_input: str
    raw_output: str | None
    parsed_output: Any | None
    quality: dict[str, Any] | None
    cached_tokens: int | None
    meal: ScanMeal | None
    revisions: list[ScanRevision]
    related: list[ScanRelatedLog]
    review: ScanReviewResponse | None


class ScanReviewRequest(BaseModel):
    verdict: ScanVerdict
    expected_calories: int | None = Field(default=None, ge=0, le=20000)
    note: str | None = Field(default=None, max_length=2000)


class ScanExportItem(BaseModel):
    log_id: int
    created_at: dt.datetime
    channel: str | None
    input_type: str
    model_name: str
    prompt_version: str
    input_text: str | None
    meal_id: int | None
    meal_name: str | None
    predicted_calories: int | None
    predicted_items: list[ScanMealItem]
    confirmed_calories: int | None
    verdict: str
    expected_calories: int | None
    note: str | None


class ScanExportResponse(BaseModel):
    generated_at: dt.datetime
    days: int
    results: list[ScanExportItem]
