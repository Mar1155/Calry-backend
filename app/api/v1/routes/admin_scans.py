"""Admin scan audit (C27): every text, voice and photo meal analysis, with the
model's raw output, quality signals, the persisted meal, and a reviewer verdict.

Scans are ai_inference_logs rows. Estimation rows are linked to the meal they
produced through ``meal_id``; older rows (logged before the link existed) still
list, just without meal context. Every detail/media/review access is written
to the admin audit log because it exposes user meal content.
"""

import datetime as dt
import json
import math
import re
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import CalryException, NotFoundException, ValidationException
from app.dependencies.admin import AdminIdentity, enforce_admin_rate_limit, get_current_admin, new_audit
from app.dependencies.db import get_db
from app.models.inference import AIInferenceLog
from app.models.meal import Meal, MealRevision
from app.models.scan_review import ScanReview
from app.schemas.admin_scans import (
    ScanChannel,
    ScanChannelSummary,
    ScanDailyPoint,
    ScanDetailResponse,
    ScanExportItem,
    ScanExportResponse,
    ScanKind,
    ScanListItem,
    ScanListResponse,
    ScanMeal,
    ScanMealItem,
    ScanRate,
    ScanRelatedLog,
    ScanReviewRequest,
    ScanReviewResponse,
    ScanRevision,
    ScanStatusFilter,
    ScanSummaryResponse,
    ScanVersionSummary,
)
from app.services.storage import read_storage_object, storage_key_from_url

router = APIRouter()

CHANNEL_INPUT_TYPES: dict[str, tuple[str, ...]] = {
    "text": ("text", "text_stream", "text_cache"),
    "voice": ("voice", "voice_stream", "voice_cache"),
    "photo": ("photo", "photo_stream", "photo_cache"),
}
ESTIMATE_TYPES: tuple[str, ...] = tuple(t for types in CHANNEL_INPUT_TYPES.values() for t in types)
REFINEMENT_TYPE = "meal_refinement"
TRANSCRIPTION_TYPE = "voice_transcription"
AUDITED_TYPES: tuple[str, ...] = (*ESTIMATE_TYPES, REFINEMENT_TYPE, TRANSCRIPTION_TYPE)
PHOTO_TYPES = CHANNEL_INPUT_TYPES["photo"]
_INPUT_TYPE_CHANNEL = {t: channel for channel, types in CHANNEL_INPUT_TYPES.items() for t in types}
_INPUT_TYPE_CHANNEL[TRANSCRIPTION_TYPE] = "voice"

# A confirmed meal whose calories the user moved by at least this much counts
# as "corrected" — the strongest real-world signal the estimate was wrong.
CORRECTION_THRESHOLD_PERCENT = 15.0
SUMMARY_ROW_LIMIT = 50_000
_URL_RE = re.compile(r"(?:https?://|/static/uploads/)\S+")
_PHOTO_INPUT_RE = re.compile(r"Hint: (?P<hint>.*?)(?: \| Additional context: (?P<ctx>.*))?$", re.DOTALL)


# ---- helpers -----------------------------------------------------------------


def _kind(input_type: str) -> str:
    if input_type in ESTIMATE_TYPES:
        return "estimate"
    if input_type == REFINEMENT_TYPE:
        return "refinement"
    if input_type == TRANSCRIPTION_TYPE:
        return "transcription"
    return "other"


def _channel(input_type: str, meal_source_type: str | None) -> str | None:
    return _INPUT_TYPE_CHANNEL.get(input_type) or meal_source_type


def _redact_urls(text: str) -> str:
    """Media is served through the audited media endpoint, never by raw URL."""
    return _URL_RE.sub("[media]", text)


def _photo_text(raw_input: str) -> str | None:
    match = _PHOTO_INPUT_RE.search(raw_input or "")
    if not match:
        return None
    parts = [
        part.strip()
        for part in (match.group("hint"), match.group("ctx"))
        if part and part.strip() and part.strip() != "None"
    ]
    return " · ".join(parts) or None


def _input_text(log: AIInferenceLog) -> str | None:
    """The user's own words for this scan, without media URLs or snapshots."""
    raw = log.raw_input or ""
    if log.input_type in PHOTO_TYPES:
        return _photo_text(raw)
    if log.input_type == TRANSCRIPTION_TYPE:
        return None
    if log.input_type == REFINEMENT_TYPE:
        _, sep, refinement = raw.partition("User refinement: ")
        return refinement.strip() if sep else None
    return _redact_urls(raw).strip() or None


def _preview(text: str | None, limit: int = 160) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _is_corrected(confirmed: int | None, correction_percent: float | None) -> bool:
    return confirmed is not None and correction_percent is not None and abs(correction_percent) >= CORRECTION_THRESHOLD_PERCENT


def _flags(
    log: AIInferenceLog,
    confirmed: int | None,
    correction_percent: float | None,
    verdict: str | None,
) -> list[str]:
    flags: list[str] = []
    if not log.success:
        flags.append("failed")
    if log.finish_reason == "length":
        flags.append("truncated")
    if log.degraded_extraction:
        flags.append("degraded")
    if log.needs_clarification:
        flags.append("clarification")
    if log.provider == "cache":
        flags.append("cache")
    if log.success and log.item_count == 1 and log.input_type in ESTIMATE_TYPES and log.provider != "cache":
        flags.append("single_item")
    quality = log.quality_json or {}
    for key in ("density_clamped", "macro_mismatch", "bias_applied"):
        if quality.get(key):
            flags.append(key)
    if _is_corrected(confirmed, correction_percent):
        flags.append("corrected")
    if verdict and verdict != "correct":
        flags.append("flagged_review")
    return flags


def _list_item(
    log: AIInferenceLog,
    meal_name: str | None,
    confirmed: int | None,
    correction_percent: float | None,
    meal_source_type: str | None,
    verdict: str | None,
) -> ScanListItem:
    return ScanListItem(
        id=log.id,
        created_at=log.created_at,
        user_id=log.user_id,
        input_type=log.input_type,
        channel=_channel(log.input_type, meal_source_type),
        kind=_kind(log.input_type),
        provider=log.provider,
        model_name=log.model_name,
        prompt_version=log.prompt_version,
        success=log.success,
        error_message=_preview(log.error_message, 300),
        finish_reason=log.finish_reason,
        latency_ms=log.latency_ms,
        prompt_tokens=log.prompt_tokens,
        completion_tokens=log.completion_tokens,
        item_count=log.item_count,
        estimated_calories=log.estimated_calories,
        confidence_score=log.confidence_score,
        degraded_extraction=log.degraded_extraction,
        needs_clarification=log.needs_clarification,
        meal_id=log.meal_id,
        meal_name=meal_name,
        confirmed_calories=confirmed,
        correction_percent=correction_percent,
        input_preview=_preview(_input_text(log)),
        flags=_flags(log, confirmed, correction_percent, verdict),
        review_verdict=verdict,
    )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower, upper = math.floor(rank), math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _int_or_none(value: float | None) -> int | None:
    return None if value is None else round(value)


def _round_or_none(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _rate(count: int, total: int) -> ScanRate:
    return ScanRate(count=count, rate=round(count / total, 4) if total else None)


def _parse_output(raw_output: str | None) -> Any | None:
    if not raw_output:
        return None
    text = raw_output.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _meal_items(meal: Meal) -> list[ScanMealItem]:
    return [
        ScanMealItem(
            name=item.name,
            quantity_estimate=item.quantity_estimate,
            weight_grams=item.weight_grams,
            calories_per_100g=item.calories_per_100g,
            estimated_calories=item.estimated_calories,
            protein_g=item.protein_g,
            carbs_g=item.carbs_g,
            fat_g=item.fat_g,
        )
        for item in meal.items
    ]


def _raw_items(parsed: Any) -> list[ScanMealItem] | None:
    """Items exactly as the model predicted them, before any user edit."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        return None
    items: list[ScanMealItem] = []
    for raw in parsed["items"]:
        if not isinstance(raw, dict):
            continue
        try:
            weight = int(round(float(raw.get("weight_grams") or 0)))
            density = float(raw.get("calories_per_100g") or 0)
        except (TypeError, ValueError):
            continue
        items.append(
            ScanMealItem(
                name=str(raw.get("name") or ""),
                quantity_estimate=raw.get("quantity_estimate"),
                weight_grams=weight,
                calories_per_100g=density,
                estimated_calories=max(0, round(weight * density / 100)),
                protein_g=raw.get("protein_g"),
                carbs_g=raw.get("carbs_g"),
                fat_g=raw.get("fat_g"),
            )
        )
    return items


def _window_start(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


def _scope_conditions(kind: ScanKind, channel: ScanChannel | None) -> list:
    if kind == "estimate":
        types = CHANNEL_INPUT_TYPES[channel] if channel else ESTIMATE_TYPES
        return [AIInferenceLog.input_type.in_(types)]
    if kind == "refinement":
        conditions = [AIInferenceLog.input_type == REFINEMENT_TYPE]
        if channel:
            conditions.append(Meal.source_type == channel)
        return conditions
    if kind == "transcription":
        conditions = [AIInferenceLog.input_type == TRANSCRIPTION_TYPE]
        if channel and channel != "voice":
            conditions.append(false())
        return conditions
    if not channel:
        return [AIInferenceLog.input_type.in_(AUDITED_TYPES)]
    options = [
        AIInferenceLog.input_type.in_(CHANNEL_INPUT_TYPES[channel]),
        and_(AIInferenceLog.input_type == REFINEMENT_TYPE, Meal.source_type == channel),
    ]
    if channel == "voice":
        options.append(AIInferenceLog.input_type == TRANSCRIPTION_TYPE)
    return [or_(*options)]


def _status_condition(status: ScanStatusFilter):
    not_degraded = or_(AIInferenceLog.degraded_extraction.is_(None), AIInferenceLog.degraded_extraction.is_(False))
    not_truncated = or_(AIInferenceLog.finish_reason.is_(None), AIInferenceLog.finish_reason != "length")
    not_clarification = or_(
        AIInferenceLog.needs_clarification.is_(None), AIInferenceLog.needs_clarification.is_(False)
    )
    return {
        "ok": and_(AIInferenceLog.success.is_(True), not_degraded, not_truncated, not_clarification),
        "failed": AIInferenceLog.success.is_(False),
        "truncated": AIInferenceLog.finish_reason == "length",
        "degraded": AIInferenceLog.degraded_extraction.is_(True),
        "clarification": AIInferenceLog.needs_clarification.is_(True),
        "corrected": and_(
            Meal.confirmed_calories.is_not(None),
            func.abs(Meal.correction_percent) >= CORRECTION_THRESHOLD_PERCENT,
        ),
        "cache": AIInferenceLog.provider == "cache",
        "single_item": and_(
            AIInferenceLog.success.is_(True),
            AIInferenceLog.item_count == 1,
            AIInferenceLog.provider != "cache",
        ),
        "unreviewed": ScanReview.id.is_(None),
        "flagged": and_(ScanReview.id.is_not(None), ScanReview.verdict != "correct"),
    }[status]


async def _load_meal(db: AsyncSession, meal_id: int | None) -> Meal | None:
    if meal_id is None:
        return None
    return await db.scalar(
        select(Meal)
        .where(Meal.id == meal_id)
        .options(selectinload(Meal.items))
        .execution_options(populate_existing=True)
    )


async def _get_scan_log(db: AsyncSession, scan_id: int) -> AIInferenceLog:
    log = await db.get(AIInferenceLog, scan_id)
    if log is None or log.input_type not in AUDITED_TYPES:
        raise NotFoundException("Scan not found.", "ADMIN_SCAN_NOT_FOUND")
    return log


def _review_response(review: ScanReview | None) -> ScanReviewResponse | None:
    if review is None:
        return None
    return ScanReviewResponse(
        verdict=review.verdict,
        expected_calories=review.expected_calories,
        note=review.note,
        reviewed_by_admin_uid=review.reviewed_by_admin_uid,
        updated_at=review.updated_at,
    )


# ---- summary -----------------------------------------------------------------


def _channel_summary(channel: str, rows: list) -> ScanChannelSummary:
    total = len(rows)
    failed = sum(1 for r in rows if not r.success)
    truncated = sum(1 for r in rows if r.finish_reason == "length")
    degraded = sum(1 for r in rows if r.degraded_extraction)
    clarification = sum(1 for r in rows if r.needs_clarification)
    cache_hits = sum(1 for r in rows if r.provider == "cache")
    model_rows = [r for r in rows if r.provider != "cache"]
    single_item = sum(1 for r in model_rows if r.success and r.item_count == 1)
    latencies = [float(r.latency_ms) for r in model_rows if r.success]
    tokens = [r.completion_tokens for r in model_rows if r.completion_tokens is not None]
    confirmed = [r for r in rows if r.confirmed_calories is not None and r.correction_percent is not None]
    abs_corrections = [abs(r.correction_percent) for r in confirmed]
    within_10 = sum(1 for value in abs_corrections if value <= 10)
    return ScanChannelSummary(
        channel=channel,
        total=total,
        failed=_rate(failed, total),
        truncated=_rate(truncated, total),
        degraded=_rate(degraded, total),
        clarification=_rate(clarification, total),
        cache_hits=_rate(cache_hits, total),
        single_item=_rate(single_item, len(model_rows)),
        median_latency_ms=_int_or_none(_percentile(latencies, 0.5)),
        p90_latency_ms=_int_or_none(_percentile(latencies, 0.9)),
        avg_completion_tokens=round(sum(tokens) / len(tokens)) if tokens else None,
        confirmed=len(confirmed),
        median_abs_correction_percent=_round_or_none(_percentile(abs_corrections, 0.5)),
        within_10_percent=_rate(within_10, len(confirmed)),
    )


@router.get("/scans/summary", response_model=ScanSummaryResponse)
async def scan_summary(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    channel: ScanChannel | None = Query(None),
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> ScanSummaryResponse:
    """Aggregate health of meal estimates. Never returns meal content."""
    enforce_admin_rate_limit(request, "scan_audit", settings.ADMIN_SCAN_RATE_LIMIT_PER_MINUTE)
    now = dt.datetime.now(dt.UTC)
    starts_at = _window_start(days)
    rows = (
        await db.execute(
            select(
                AIInferenceLog.input_type,
                AIInferenceLog.provider,
                AIInferenceLog.model_name,
                AIInferenceLog.prompt_version,
                AIInferenceLog.success,
                AIInferenceLog.finish_reason,
                AIInferenceLog.latency_ms,
                AIInferenceLog.completion_tokens,
                AIInferenceLog.item_count,
                AIInferenceLog.degraded_extraction,
                AIInferenceLog.needs_clarification,
                AIInferenceLog.created_at,
                Meal.confirmed_calories,
                Meal.correction_percent,
                ScanReview.verdict,
            )
            .outerjoin(Meal, Meal.id == AIInferenceLog.meal_id)
            .outerjoin(ScanReview, ScanReview.inference_log_id == AIInferenceLog.id)
            .where(AIInferenceLog.created_at >= starts_at, *_scope_conditions("estimate", channel))
            .order_by(AIInferenceLog.created_at.desc())
            .limit(SUMMARY_ROW_LIMIT + 1)
        )
    ).all()
    truncated_window = len(rows) > SUMMARY_ROW_LIMIT
    rows = rows[:SUMMARY_ROW_LIMIT]

    by_channel: dict[str, list] = defaultdict(list)
    for row in rows:
        by_channel[_INPUT_TYPE_CHANNEL.get(row.input_type, "other")].append(row)

    by_version: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        if row.provider != "cache":
            by_version[(row.model_name, row.prompt_version)].append(row)
    versions = []
    for (model_name, prompt_version), group in by_version.items():
        total = len(group)
        corrections = [
            abs(r.correction_percent)
            for r in group
            if r.confirmed_calories is not None and r.correction_percent is not None
        ]
        reviewed = [r for r in group if r.verdict]
        versions.append(
            ScanVersionSummary(
                model_name=model_name,
                prompt_version=prompt_version,
                total=total,
                failure_rate=_rate(sum(1 for r in group if not r.success), total).rate,
                degraded_rate=_rate(sum(1 for r in group if r.degraded_extraction), total).rate,
                truncation_rate=_rate(sum(1 for r in group if r.finish_reason == "length"), total).rate,
                median_latency_ms=_int_or_none(_percentile([float(r.latency_ms) for r in group if r.success], 0.5)),
                median_abs_correction_percent=_round_or_none(_percentile(corrections, 0.5)),
                flagged_reviews=sum(1 for r in reviewed if r.verdict != "correct"),
                reviewed=len(reviewed),
            )
        )
    versions.sort(key=lambda v: v.total, reverse=True)

    daily_counts: dict[dt.date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=dt.UTC)
        bucket = daily_counts[created.astimezone(dt.UTC).date()]
        bucket["total"] += 1
        bucket["failed"] += 0 if row.success else 1
        bucket["degraded"] += 1 if row.degraded_extraction else 0
        bucket["truncated"] += 1 if row.finish_reason == "length" else 0
    daily = []
    for offset in range(days, -1, -1):
        day = (now - dt.timedelta(days=offset)).date()
        counts = daily_counts.get(day, {})
        daily.append(
            ScanDailyPoint(
                date=day,
                total=counts.get("total", 0),
                failed=counts.get("failed", 0),
                degraded=counts.get("degraded", 0),
                truncated=counts.get("truncated", 0),
            )
        )

    verdict_rows = (
        await db.execute(
            select(ScanReview.verdict, func.count())
            .join(AIInferenceLog, AIInferenceLog.id == ScanReview.inference_log_id)
            .outerjoin(Meal, Meal.id == AIInferenceLog.meal_id)
            .where(AIInferenceLog.created_at >= starts_at, *_scope_conditions("estimate", channel))
            .group_by(ScanReview.verdict)
        )
    ).all()
    verdicts = {verdict: int(count) for verdict, count in verdict_rows}

    db.add(new_audit(request, admin, "scan_summary_viewed", "success", metadata={"days": days, "channel": channel}))
    return ScanSummaryResponse(
        days=days,
        starts_at=starts_at,
        generated_at=now,
        overall=_channel_summary("all", rows),
        channels=[_channel_summary(name, by_channel.get(name, [])) for name in CHANNEL_INPUT_TYPES],
        versions=versions,
        daily=daily,
        verdicts=verdicts,
        reviewed=sum(verdicts.values()),
        truncated_window=truncated_window,
        row_limit=SUMMARY_ROW_LIMIT,
    )


# ---- list / export -----------------------------------------------------------


@router.get("/scans", response_model=ScanListResponse)
async def list_scans(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    kind: ScanKind = Query("estimate"),
    channel: ScanChannel | None = Query(None),
    status: ScanStatusFilter | None = Query(None),
    model: str | None = Query(None, max_length=100),
    prompt_version: str | None = Query(None, max_length=100),
    user_id: int | None = Query(None, ge=1),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> ScanListResponse:
    enforce_admin_rate_limit(request, "scan_audit", settings.ADMIN_SCAN_RATE_LIMIT_PER_MINUTE)
    conditions = [AIInferenceLog.created_at >= _window_start(days), *_scope_conditions(kind, channel)]
    if status:
        conditions.append(_status_condition(status))
    if model:
        conditions.append(AIInferenceLog.model_name == model)
    if prompt_version:
        conditions.append(AIInferenceLog.prompt_version == prompt_version)
    if user_id:
        conditions.append(AIInferenceLog.user_id == user_id)
    term = (q or "").strip()
    if term:
        if len(term) < 2:
            raise ValidationException("Enter at least 2 characters.", "SCAN_QUERY_TOO_SHORT")
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions.append(
            or_(
                Meal.meal_name.ilike(pattern, escape="\\"),
                AIInferenceLog.raw_input.ilike(pattern, escape="\\"),
            )
        )

    def scoped(stmt):
        return (
            stmt.outerjoin(Meal, Meal.id == AIInferenceLog.meal_id)
            .outerjoin(ScanReview, ScanReview.inference_log_id == AIInferenceLog.id)
            .where(*conditions)
        )

    total = int((await db.execute(scoped(select(func.count()).select_from(AIInferenceLog)))).scalar_one())
    rows = (
        await db.execute(
            scoped(
                select(
                    AIInferenceLog,
                    Meal.meal_name,
                    Meal.confirmed_calories,
                    Meal.correction_percent,
                    Meal.source_type,
                    ScanReview.verdict,
                )
            )
            .order_by(AIInferenceLog.created_at.desc(), AIInferenceLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    db.add(
        new_audit(
            request,
            admin,
            "scan_list_viewed",
            "success",
            target_user_id=user_id,
            metadata={
                "days": days,
                "kind": kind,
                "channel": channel,
                "status": status,
                "has_query": bool(term),
                "result_count": len(rows),
            },
        )
    )
    return ScanListResponse(
        results=[_list_item(*row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/scans/export", response_model=ScanExportResponse)
async def export_reviewed_scans(
    request: Request,
    days: int = Query(90, ge=1, le=365),
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> ScanExportResponse:
    """Reviewed estimates as a labelled dataset for prompt/model evaluation."""
    enforce_admin_rate_limit(request, "scan_export", settings.ADMIN_SEARCH_RATE_LIMIT_PER_MINUTE)
    rows = (
        await db.execute(
            select(AIInferenceLog, ScanReview, Meal)
            .join(ScanReview, ScanReview.inference_log_id == AIInferenceLog.id)
            .outerjoin(Meal, Meal.id == AIInferenceLog.meal_id)
            .where(
                AIInferenceLog.created_at >= _window_start(days),
                AIInferenceLog.input_type.in_(ESTIMATE_TYPES),
            )
            .order_by(AIInferenceLog.created_at.desc())
            .options(selectinload(Meal.items))
            .execution_options(populate_existing=True)
            .limit(5000)
        )
    ).all()
    results = []
    for log, review, meal in rows:
        predicted = _raw_items(_parse_output(log.raw_output))
        if predicted is None and meal is not None:
            predicted = _meal_items(meal)
        results.append(
            ScanExportItem(
                log_id=log.id,
                created_at=log.created_at,
                channel=_channel(log.input_type, meal.source_type if meal else None),
                input_type=log.input_type,
                model_name=log.model_name,
                prompt_version=log.prompt_version,
                input_text=_input_text(log),
                meal_id=meal.id if meal else None,
                meal_name=meal.meal_name if meal else None,
                predicted_calories=log.estimated_calories,
                predicted_items=predicted or [],
                confirmed_calories=meal.confirmed_calories if meal else None,
                verdict=review.verdict,
                expected_calories=review.expected_calories,
                note=review.note,
            )
        )
    db.add(new_audit(request, admin, "scan_export", "success", metadata={"days": days, "result_count": len(results)}))
    return ScanExportResponse(generated_at=dt.datetime.now(dt.UTC), days=days, results=results)


# ---- detail / media / review -------------------------------------------------


@router.get("/scans/{scan_id}", response_model=ScanDetailResponse)
async def get_scan(
    scan_id: int,
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> ScanDetailResponse:
    enforce_admin_rate_limit(request, "scan_audit", settings.ADMIN_SCAN_RATE_LIMIT_PER_MINUTE)
    log = await _get_scan_log(db, scan_id)
    meal = await _load_meal(db, log.meal_id)
    review = await db.scalar(select(ScanReview).where(ScanReview.inference_log_id == log.id))

    revisions: list[MealRevision] = []
    related: list[AIInferenceLog] = []
    if meal is not None:
        revisions = list(
            await db.scalars(
                select(MealRevision).where(MealRevision.meal_id == meal.id).order_by(MealRevision.created_at)
            )
        )
        related = list(
            await db.scalars(
                select(AIInferenceLog)
                .where(AIInferenceLog.meal_id == meal.id, AIInferenceLog.id != log.id)
                .order_by(AIInferenceLog.created_at)
            )
        )

    db.add(
        new_audit(
            request,
            admin,
            "scan_viewed",
            "success",
            target_user_id=log.user_id,
            metadata={"scan_id": log.id, "meal_id": log.meal_id},
        )
    )
    return ScanDetailResponse(
        scan=_list_item(
            log,
            meal.meal_name if meal else None,
            meal.confirmed_calories if meal else None,
            meal.correction_percent if meal else None,
            meal.source_type if meal else None,
            review.verdict if review else None,
        ),
        raw_input=_redact_urls(log.raw_input or ""),
        raw_output=log.raw_output,
        parsed_output=_parse_output(log.raw_output),
        quality=log.quality_json,
        cached_tokens=log.cached_tokens,
        meal=(
            ScanMeal(
                id=meal.id,
                source_type=meal.source_type,
                meal_name=meal.meal_name,
                original_input=_redact_urls(meal.original_input),
                meal_category=meal.meal_category,
                estimated_calories=meal.estimated_calories,
                estimated_min_calories=meal.estimated_min_calories,
                estimated_max_calories=meal.estimated_max_calories,
                confirmed_calories=meal.confirmed_calories,
                correction_percent=meal.correction_percent,
                total_protein_g=meal.total_protein_g,
                total_carbs_g=meal.total_carbs_g,
                total_fat_g=meal.total_fat_g,
                ai_confidence=meal.ai_confidence,
                confidence_score=meal.confidence_score,
                needs_clarification=meal.needs_clarification,
                clarifying_question=meal.clarifying_question,
                has_image=bool(meal.image_url),
                has_audio=bool(meal.audio_url),
                created_at=meal.created_at,
                confirmed_at=meal.confirmed_at,
                items=_meal_items(meal),
            )
            if meal
            else None
        ),
        revisions=[
            ScanRevision(
                id=r.id,
                refinement_type=r.refinement_type,
                user_input=r.user_input,
                previous_calories=r.previous_calories,
                revised_calories=r.revised_calories,
                calorie_delta=r.calorie_delta,
                ai_summary=r.ai_summary,
                model_name=r.model_name,
                prompt_version=r.prompt_version,
                created_at=r.created_at,
            )
            for r in revisions
        ],
        related=[
            ScanRelatedLog(
                id=r.id,
                input_type=r.input_type,
                created_at=r.created_at,
                success=r.success,
                model_name=r.model_name,
                latency_ms=r.latency_ms,
                raw_output_preview=_preview(r.raw_output, 240),
            )
            for r in related
        ],
        review=_review_response(review),
    )


@router.get("/scans/{scan_id}/media")
async def get_scan_media(
    scan_id: int,
    request: Request,
    kind: str = Query("image", pattern="^(image|audio)$"),
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Proxy the meal's photo or voice note so admins never need a raw storage URL."""
    enforce_admin_rate_limit(request, "scan_media", settings.ADMIN_SCAN_RATE_LIMIT_PER_MINUTE)
    log = await _get_scan_log(db, scan_id)
    meal = await _load_meal(db, log.meal_id)
    url = (meal.image_url if kind == "image" else meal.audio_url) if meal else None
    key = storage_key_from_url(url) if url else None
    if key is None:
        raise NotFoundException("No media is available for this scan.", "ADMIN_SCAN_MEDIA_UNAVAILABLE")
    try:
        found = await read_storage_object(key, max_bytes=settings.MEAL_UPLOAD_MAX_BYTES)
    except ValueError as exc:
        raise NotFoundException("No media is available for this scan.", "ADMIN_SCAN_MEDIA_UNAVAILABLE") from exc
    except RuntimeError as exc:
        raise CalryException("Media storage is unavailable.", 503, "ADMIN_SCAN_MEDIA_STORAGE_UNAVAILABLE") from exc
    if found is None:
        raise NotFoundException("No media is available for this scan.", "ADMIN_SCAN_MEDIA_UNAVAILABLE")
    data, content_type = found
    if not content_type.startswith(f"{kind}/"):
        raise NotFoundException("No media is available for this scan.", "ADMIN_SCAN_MEDIA_UNAVAILABLE")

    db.add(
        new_audit(
            request,
            admin,
            "scan_media_viewed",
            "success",
            target_user_id=log.user_id,
            metadata={"scan_id": log.id, "meal_id": log.meal_id, "kind": kind},
        )
    )
    await db.commit()
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/scans/{scan_id}/review", response_model=ScanReviewResponse)
async def review_scan(
    scan_id: int,
    payload: ScanReviewRequest,
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> ScanReviewResponse:
    enforce_admin_rate_limit(request, "scan_review", settings.ADMIN_SCAN_RATE_LIMIT_PER_MINUTE)
    log = await _get_scan_log(db, scan_id)
    review = await db.scalar(select(ScanReview).where(ScanReview.inference_log_id == log.id))
    previous = review.verdict if review else None
    note = payload.note.strip() if payload.note and payload.note.strip() else None
    if review is None:
        review = ScanReview(inference_log_id=log.id, user_id=log.user_id, reviewed_by_admin_uid=admin.uid, verdict=payload.verdict)
        db.add(review)
    review.verdict = payload.verdict
    review.expected_calories = payload.expected_calories
    review.note = note
    review.reviewed_by_admin_uid = admin.uid
    review.updated_at = dt.datetime.now(dt.UTC)
    db.add(
        new_audit(
            request,
            admin,
            "scan_reviewed",
            "success",
            target_user_id=log.user_id,
            metadata={"scan_id": log.id, "verdict": payload.verdict, "previous_verdict": previous},
        )
    )
    await db.commit()
    await db.refresh(review)
    return _review_response(review)  # type: ignore[return-value]


@router.post("/scans/{scan_id}/review/clear", status_code=204)
async def clear_scan_review(
    scan_id: int,
    request: Request,
    admin: AdminIdentity = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    enforce_admin_rate_limit(request, "scan_review", settings.ADMIN_SCAN_RATE_LIMIT_PER_MINUTE)
    log = await _get_scan_log(db, scan_id)
    review = await db.scalar(select(ScanReview).where(ScanReview.inference_log_id == log.id))
    if review is not None:
        db.add(
            new_audit(
                request,
                admin,
                "scan_review_cleared",
                "success",
                target_user_id=log.user_id,
                metadata={"scan_id": log.id, "previous_verdict": review.verdict},
            )
        )
        await db.delete(review)
        await db.commit()
    return Response(status_code=204)
