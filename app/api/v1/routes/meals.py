import datetime as dt
import json
import logging
import time

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.schemas.meal_estimate import MealEstimateResult, UserContext
from app.ai.services.calorie_estimation_service import AICalorieEstimationService
from app.ai.streaming import protocol
from app.core.config import settings
from app.db.session import SessionLocal
from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.premium import (
    ensure_history_date_access,
    free_history_cutoff,
    has_premium_access,
)
from app.models.meal import Meal, MealItem, MealRevision
from app.models.meal_analysis import MealAnalysisJob
from app.models.user import User
from app.repositories.food_memory import FoodMemoryRepository
from app.repositories.meal import MealRepository
from app.schemas.meal import (
    MealCreatePhoto,
    MealCreateText,
    MealCreateVoice,
    MealPhotoAnalysisCreate,
    MealPhotoAnalysisStartResponse,
    MealPhotoAnalysisStatusResponse,
    MealRefineRequest,
    MealResponse,
    MealUpdate,
)
from app.services import stream_bus
from app.services.meal_invariants import (
    InvalidMealIngredients,
    enforce_estimate_ingredient_invariants,
)
from app.services.storage import save_upload
from app.services.summary import SummaryService

logger = logging.getLogger("app.api.meals")
router = APIRouter()


def _time_based_meal_category(now: dt.datetime | None = None) -> str:
    hour = (now or dt.datetime.now(dt.UTC)).hour
    if 5 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 16:
        return "lunch"
    if 16 <= hour < 19:
        return "snack"
    if hour >= 19:
        return "dinner"
    return "snack"


def _resolve_meal_category(explicit_category: str | None, estimation: MealEstimateResult) -> str:
    """Manual choice wins; an uncertain AI label never overrides the clock."""
    if explicit_category:
        return explicit_category
    if estimation.meal_category_suggestion is not None and estimation.meal_category_confidence in {"medium", "high"}:
        return estimation.meal_category_suggestion
    return _time_based_meal_category()


async def _process_and_save_meal(
    db: AsyncSession,
    user: User,
    source_type: str,
    original_input: str,
    image_url: str | None,
    audio_url: str | None,
    estimation: MealEstimateResult,
    client_request_id: str | None = None,
    meal_category: str | None = None,
) -> Meal:
    """Helper method to construct a Meal record with children and trigger summary sync."""
    meal_repo = MealRepository(db)
    estimation = enforce_estimate_ingredient_invariants(estimation)

    # 1. Instantiate Core Meal entity with new AI pipeline fields
    meal = Meal(
        user_id=user.id,
        source_type=source_type,
        original_input=original_input,
        image_url=image_url,
        audio_url=audio_url,
        meal_name=estimation.meal_name,
        meal_category=_resolve_meal_category(meal_category, estimation),
        meal_category_suggestion=estimation.meal_category_suggestion,
        meal_category_confidence=estimation.meal_category_confidence,
        estimated_calories=estimation.estimated_calories,
        estimated_min_calories=estimation.estimated_min_calories,
        estimated_max_calories=estimation.estimated_max_calories,
        total_protein_g=estimation.total_protein_g,
        total_carbs_g=estimation.total_carbs_g,
        total_fat_g=estimation.total_fat_g,
        estimation_reasoning=estimation.estimation_reasoning,
        confirmed_calories=None,  # Not yet validated by user
        ai_confidence=estimation.confidence,
        confidence_score=estimation.confidence_score,
        needs_clarification=estimation.needs_clarification,
        clarifying_question=estimation.clarifying_question,
        client_request_id=client_request_id,
    )
    await meal_repo.create(meal)
    await db.flush()  # Assures meal.id is populated

    # 2. Instantiate Child Food Item details and add directly to session
    for item in estimation.items:
        meal_item = MealItem(
            meal_id=meal.id,
            name=item.name,
            quantity_estimate=item.quantity_estimate,
            weight_grams=item.weight_grams,
            calories_per_100g=item.calories_per_100g,
            protein_g=item.protein_g,
            carbs_g=item.carbs_g,
            fat_g=item.fat_g,
        )
        db.add(meal_item)

    await db.flush()

    # 3. Synchronize User's Daily Summary for this meal's creation date
    try:
        # Summary is secondary to returning the analysis. Isolate it in a
        # savepoint so a summary write failure cannot poison meal persistence.
        async with db.begin_nested():
            summary_service = SummaryService(db)
            meal_date = meal.created_at.date()
            await summary_service.sync_daily_summary(user.id, meal_date)
    except Exception:
        logger.exception("Failed to synchronize daily summary on meal log")

    # 4. Asynchronously retrieve the completed Meal with preloaded items to prevent lazyloading issues
    from sqlalchemy.future import select
    from sqlalchemy.orm import selectinload

    stmt = select(Meal).where(Meal.id == meal.id).options(selectinload(Meal.items))
    result = await db.execute(stmt)
    return result.scalar_one()


def _primary_locale(accept_language: str | None) -> str:
    if not accept_language:
        return "en"
    primary = accept_language.split(",")[0].split("-")[0].split("_")[0].strip().lower()
    return primary if primary in {"en", "it", "es", "zh", "ja", "ar"} else "en"


async def _build_user_context(
    db: AsyncSession,
    user: User,
    locale: str | None = None,
) -> UserContext:
    """Builds UserContext from a SINGLE correction-history query (C5): the prose
    summary for the prompt and the deterministic per-source bias for C11."""
    from app.ai.services.correction_context_service import AICorrectionContextService

    correction_service = AICorrectionContextService(db)
    context = await correction_service.get_correction_context(user.id)

    return UserContext(
        daily_calorie_goal=user.daily_calorie_goal,
        locale=locale,
        timezone=None,
        previous_corrections_summary=context.summary,
        sex=user.sex,
        age=user.age,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        goal_type=user.goal_type,
        correction_bias_by_source=context.bias_by_source or None,
    )


async def _find_existing_by_request_id(db: AsyncSession, user_id: int, client_request_id: str | None) -> Meal | None:
    """Idempotency lookup (C13): a repeat with the same client_request_id returns
    the already-created meal instead of re-running the LLM."""
    if not client_request_id:
        return None
    from sqlalchemy.future import select
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Meal)
        .where(Meal.user_id == user_id, Meal.client_request_id == client_request_id)
        .options(selectinload(Meal.items))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _find_existing_analysis_by_request_id(
    db: AsyncSession, user_id: int, client_request_id: str | None
) -> MealAnalysisJob | None:
    if not client_request_id:
        return None
    stmt = select(MealAnalysisJob).where(
        MealAnalysisJob.user_id == user_id,
        MealAnalysisJob.client_request_id == client_request_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _analysis_status_response(db: AsyncSession, job: MealAnalysisJob) -> MealPhotoAnalysisStatusResponse:
    meal = None
    if job.meal_id is not None:
        result = await db.execute(select(Meal).where(Meal.id == job.meal_id).options(selectinload(Meal.items)))
        meal = result.scalar_one_or_none()
    return MealPhotoAnalysisStatusResponse(
        id=job.id,
        status=job.status,
        meal_id=job.meal_id,
        meal=meal,
        error_code="analysis_failed" if job.status == "failed" else None,
        error_message=(
            "We couldn't analyze this photo. Try again or add a short description." if job.status == "failed" else None
        ),
        attempts=job.attempts,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _meal_items_payload(meal: Meal) -> list[dict]:
    return [
        {
            "name": item.name,
            "quantity_estimate": item.quantity_estimate,
            "weight_grams": item.weight_grams,
            "calories_per_100g": item.calories_per_100g,
            "protein_g": item.protein_g,
            "carbs_g": item.carbs_g,
            "fat_g": item.fat_g,
            "estimated_calories": item.estimated_calories,
        }
        for item in meal.items
    ]


def _meal_snapshot(meal: Meal) -> dict:
    return {
        "meal_name": meal.meal_name,
        "estimated_calories": meal.estimated_calories,
        "estimated_min_calories": meal.estimated_min_calories,
        "estimated_max_calories": meal.estimated_max_calories,
        "total_protein_g": meal.total_protein_g,
        "total_carbs_g": meal.total_carbs_g,
        "total_fat_g": meal.total_fat_g,
        "confidence": meal.ai_confidence,
        "meal_category": meal.meal_category,
        "source_type": meal.source_type,
        "original_input": meal.original_input,
        "items": _meal_items_payload(meal),
        "estimation_reasoning": meal.estimation_reasoning,
    }


def _meal_response_dict(meal: Meal, estimation: MealEstimateResult) -> dict:
    return {
        "id": meal.id,
        "user_id": meal.user_id,
        "source_type": meal.source_type,
        "original_input": meal.original_input,
        "image_url": meal.image_url,
        "audio_url": meal.audio_url,
        "meal_name": estimation.meal_name,
        "meal_category": (
            estimation.meal_category_suggestion
            if estimation.meal_category_confidence in {"medium", "high"}
            else meal.meal_category
        ),
        "meal_category_suggestion": estimation.meal_category_suggestion,
        "meal_category_confidence": estimation.meal_category_confidence,
        "estimated_calories": estimation.estimated_calories,
        "estimated_min_calories": estimation.estimated_min_calories,
        "estimated_max_calories": estimation.estimated_max_calories,
        "total_protein_g": estimation.total_protein_g,
        "total_carbs_g": estimation.total_carbs_g,
        "total_fat_g": estimation.total_fat_g,
        "estimation_reasoning": estimation.estimation_reasoning,
        "confirmed_calories": meal.confirmed_calories,
        "ai_confidence": estimation.confidence,
        "confidence_score": estimation.confidence_score,
        "needs_clarification": estimation.needs_clarification,
        "clarifying_question": estimation.clarifying_question,
        "created_at": meal.created_at,
        "confirmed_at": meal.confirmed_at,
        "items": [
            {
                "id": idx + 1,
                "meal_id": meal.id,
                "name": item.name,
                "estimated_calories": item.estimated_calories,
                "quantity_estimate": item.quantity_estimate,
                "weight_grams": item.weight_grams,
                "calories_per_100g": item.calories_per_100g,
                "protein_g": item.protein_g,
                "carbs_g": item.carbs_g,
                "fat_g": item.fat_g,
                "created_at": meal.created_at,
            }
            for idx, item in enumerate(estimation.items)
        ],
        "ai_summary": estimation.ai_summary,
        "refinement_changes": estimation.changes_made,
    }


# NDJSON streaming responses must not be buffered by the ASGI server or any
# reverse proxy, or the client sees the whole payload at once instead of a live
# stream. X-Accel-Buffering disables nginx buffering; no-cache is belt-and-braces.
_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _serialize_meal(meal: Meal) -> dict:
    """Full MealResponse contract as a JSON-ready dict (for the `done` event)."""
    return MealResponse.model_validate(meal).model_dump(mode="json")


@router.post("/text", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_meal_via_text(
    payload: MealCreateText,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> Meal:
    """Logs a meal by parsing free-form written text."""
    # 0. Idempotency: return the existing meal on a repeat request id.
    existing = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
    if existing is not None:
        return existing

    ai_service = AICalorieEstimationService(db)

    # 1. Build user context
    user_context = await _build_user_context(
        db,
        current_user,
        _primary_locale(accept_language),
    )

    # 2. Fetch calorie estimation from AI Orchestrator
    estimation = await ai_service.estimate_from_text(
        text=payload.text,
        user_context=user_context,
        user_id=current_user.id,
        additional_context=payload.additional_context,
    )

    # 3. Build and commit the entities
    meal = await _process_and_save_meal(
        db=db,
        user=current_user,
        source_type="text",
        original_input=payload.text,
        image_url=None,
        audio_url=None,
        estimation=estimation,
        client_request_id=payload.client_request_id,
        meal_category=payload.meal_category,
    )
    return meal


@router.post("/photo", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_meal_via_photo(
    payload: MealCreatePhoto,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> Meal:
    """Logs a meal by analyzing an image URL with optional text description."""
    # 0. Idempotency: return the existing meal on a repeat request id.
    existing = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
    if existing is not None:
        return existing

    ai_service = AICalorieEstimationService(db)

    # 1. Build user context
    user_context = await _build_user_context(
        db,
        current_user,
        _primary_locale(accept_language),
    )

    # 2. Fetch calorie estimation using Multimodal Vision AI
    estimation = await ai_service.estimate_from_image(
        image_url=payload.image_url,
        optional_hint=payload.text,
        user_context=user_context,
        user_id=current_user.id,
        additional_context=payload.additional_context,
    )

    # 3. Build and commit entities
    raw_desc = estimation.meal_name.strip() or (payload.text or "Meal photo")
    meal = await _process_and_save_meal(
        db=db,
        user=current_user,
        source_type="photo",
        original_input=raw_desc,
        image_url=payload.image_url,
        audio_url=None,
        estimation=estimation,
        client_request_id=payload.client_request_id,
        meal_category=payload.meal_category,
    )
    return meal


@router.post("/photo/analysis", response_model=MealPhotoAnalysisStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_photo_analysis(
    payload: MealPhotoAnalysisCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> MealPhotoAnalysisStartResponse:
    existing_job = await _find_existing_analysis_by_request_id(db, current_user.id, payload.client_request_id)
    if existing_job is not None:
        return MealPhotoAnalysisStartResponse(
            id=existing_job.id,
            status=existing_job.status,
            meal_id=existing_job.meal_id,
        )

    existing_meal = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
    job = MealAnalysisJob(
        user_id=current_user.id,
        status="completed" if existing_meal is not None else "queued",
        source_type="photo",
        image_url=payload.image_url,
        text=payload.text,
        additional_context=payload.additional_context,
        meal_category=payload.meal_category,
        locale=_primary_locale(accept_language),
        client_request_id=payload.client_request_id,
        meal_id=existing_meal.id if existing_meal is not None else None,
        completed_at=dt.datetime.now(dt.UTC) if existing_meal is not None else None,
    )
    db.add(job)
    await db.flush()
    await db.commit()

    if existing_meal is None:
        try:
            from app.tasks.meal_analysis import analyze_photo_meal

            logger.info(
                "event=meal_analysis_enqueue_started transport=poll job_id=%s user_id=%s client_request_id=%s",
                job.id,
                current_user.id,
                payload.client_request_id,
            )
            task = analyze_photo_meal.delay(job.id)
            job.celery_task_id = task.id
            await db.commit()
            logger.info(
                "event=meal_analysis_enqueue_succeeded transport=poll job_id=%s user_id=%s celery_task_id=%s",
                job.id,
                current_user.id,
                task.id,
            )
        except Exception as exc:
            logger.exception(
                "event=meal_analysis_queue_unavailable transport=poll job_id=%s user_id=%s "
                "client_request_id=%s job_status=%s error_type=%s error=%s",
                job.id,
                current_user.id,
                payload.client_request_id,
                job.status,
                type(exc).__name__,
                str(exc),
            )
            job.status = "failed"
            job.error_message = str(exc)
            await db.commit()
            logger.error(
                "event=meal_analysis_queue_failure_persisted transport=poll job_id=%s user_id=%s job_status=%s",
                job.id,
                current_user.id,
                job.status,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Meal analysis queue is unavailable. Try again shortly.",
            )

    return MealPhotoAnalysisStartResponse(id=job.id, status=job.status, meal_id=job.meal_id)


@router.get("/photo/analysis/{analysis_id}", response_model=MealPhotoAnalysisStatusResponse)
async def get_photo_analysis(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MealPhotoAnalysisStatusResponse:
    result = await db.execute(
        select(MealAnalysisJob).where(
            MealAnalysisJob.id == analysis_id,
            MealAnalysisJob.user_id == current_user.id,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal analysis not found.",
        )
    return await _analysis_status_response(db, job)


@router.delete("/photo/analysis/request/{client_request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_photo_analysis(
    client_request_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Cancel the durable photo job started by this client request.

    The DB state is changed first so a task racing toward persistence can see
    the cancellation. Celery termination then interrupts an already-running
    task; revoking alone would only prevent queued tasks from starting.
    """
    job = await _find_existing_analysis_by_request_id(db, current_user.id, client_request_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal analysis not found.")
    if job.status in {"failed", "cancelled"}:
        return

    # The UI can still show "processing" for a few milliseconds after the
    # worker commits. Honor the user's X in that race by removing the resulting
    # unconfirmed meal as part of the same cancellation operation.
    if job.status == "completed" and job.meal_id is not None:
        meal = await db.get(Meal, job.meal_id)
        if meal is not None and meal.confirmed_calories is None:
            meal_date = meal.created_at.date()
            await db.delete(meal)
            await db.flush()
            await SummaryService(db).sync_daily_summary(current_user.id, meal_date)
        job.status = "cancelled"
        job.meal_id = None
        job.completed_at = dt.datetime.now(dt.UTC)
        await db.commit()
        return

    job.status = "cancelled"
    job.error_message = None
    job.completed_at = dt.datetime.now(dt.UTC)
    task_id = job.celery_task_id
    await db.commit()

    if task_id:
        from app.worker.celery_app import celery_app

        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    logger.info(
        "event=meal_analysis_cancelled job_id=%s user_id=%s celery_task_id=%s",
        job.id,
        current_user.id,
        task_id,
    )


@router.post("/voice", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_meal_via_voice(
    payload: MealCreateVoice,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> Meal:
    """Logs a meal by transcribing a recorded audio file URL and parsing the text."""
    # 0. Idempotency: return the existing meal on a repeat request id.
    existing = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
    if existing is not None:
        return existing

    ai_service = AICalorieEstimationService(db)

    # 1. Build user context
    user_context = await _build_user_context(
        db,
        current_user,
        _primary_locale(accept_language),
    )

    # 2. Transcribe voice note and estimate caloric components
    transcript, estimation = await ai_service.estimate_from_voice(
        audio_url=payload.audio_url,
        user_context=user_context,
        user_id=current_user.id,
        additional_context=payload.additional_context,
    )

    # 3. Build and commit entities
    meal = await _process_and_save_meal(
        db=db,
        user=current_user,
        source_type="voice",
        original_input=transcript,
        image_url=None,
        audio_url=payload.audio_url,
        estimation=estimation,
        client_request_id=payload.client_request_id,
        meal_category=payload.meal_category,
    )
    return meal


@router.post("/text/stream")
async def stream_log_meal_via_text(
    payload: MealCreateText,
    current_user: User = Depends(get_current_user),
    accept_language: str | None = Header(default="en"),
) -> StreamingResponse:
    """Streams a text meal analysis as NDJSON: status -> meal_name -> item* -> done.

    The final `done` event carries the persisted MealResponse; the meal is only
    written to the DB once the full estimate is validated. A client disconnect
    mid-stream cancels the generator and persists nothing.
    """

    async def gen():
        async with SessionLocal() as db:
            try:
                existing = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
                if existing is not None:
                    yield protocol.line(protocol.done(_serialize_meal(existing)))
                    return

                ai_service = AICalorieEstimationService(db)
                user_context = await _build_user_context(db, current_user, _primary_locale(accept_language))
                yield protocol.line(protocol.status("processing"))

                estimation: MealEstimateResult | None = None
                async for ev in ai_service.stream_estimate_from_text(
                    payload.text,
                    user_context=user_context,
                    user_id=current_user.id,
                    additional_context=payload.additional_context,
                ):
                    if ev.get("type") == "__complete__":
                        estimation = ev["result"]
                    else:
                        yield protocol.line(ev)

                if estimation is None:
                    raise RuntimeError("stream produced no result")

                meal = await _process_and_save_meal(
                    db=db,
                    user=current_user,
                    source_type="text",
                    original_input=payload.text,
                    image_url=None,
                    audio_url=None,
                    estimation=estimation,
                    client_request_id=payload.client_request_id,
                    meal_category=payload.meal_category,
                )
                await db.commit()
                yield protocol.line(protocol.done(_serialize_meal(meal)))
            except Exception:  # noqa: BLE001 — surface as a protocol error, never a 500 mid-stream
                logger.exception("Text meal stream failed")
                await db.rollback()
                yield protocol.line(protocol.error("stream_failed", "AI inference failed. Please try again."))

    return StreamingResponse(gen(), media_type=protocol.NDJSON_MEDIA_TYPE, headers=_STREAM_HEADERS)


@router.post("/voice/stream")
async def stream_log_meal_via_voice(
    payload: MealCreateVoice,
    current_user: User = Depends(get_current_user),
    accept_language: str | None = Header(default="en"),
) -> StreamingResponse:
    """Streams a voice meal analysis: status(transcribing) -> status(processing)
    -> meal_name -> item* -> done. Transcription runs first, then the transcript
    flows through the same streaming text pipeline."""

    async def gen():
        async with SessionLocal() as db:
            try:
                existing = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
                if existing is not None:
                    yield protocol.line(protocol.done(_serialize_meal(existing)))
                    return

                ai_service = AICalorieEstimationService(db)
                user_context = await _build_user_context(db, current_user, _primary_locale(accept_language))

                yield protocol.line(protocol.status("transcribing"))
                transcription = await ai_service.speech_service.transcribe_audio(
                    audio_url=payload.audio_url,
                    user_id=current_user.id,
                )
                transcript = transcription.transcript

                yield protocol.line(protocol.status("processing"))
                estimation: MealEstimateResult | None = None
                async for ev in ai_service.stream_estimate_from_text(
                    transcript,
                    user_context=user_context,
                    user_id=current_user.id,
                    is_voice=True,
                    channel="voice",
                    transcription_confidence=transcription.confidence,
                    additional_context=payload.additional_context,
                ):
                    if ev.get("type") == "__complete__":
                        estimation = ev["result"]
                    else:
                        yield protocol.line(ev)

                if estimation is None:
                    raise RuntimeError("stream produced no result")

                meal = await _process_and_save_meal(
                    db=db,
                    user=current_user,
                    source_type="voice",
                    original_input=transcript,
                    image_url=None,
                    audio_url=payload.audio_url,
                    estimation=estimation,
                    client_request_id=payload.client_request_id,
                    meal_category=payload.meal_category,
                )
                await db.commit()
                yield protocol.line(protocol.done(_serialize_meal(meal)))
            except Exception:  # noqa: BLE001
                logger.exception("Voice meal stream failed")
                await db.rollback()
                yield protocol.line(protocol.error("stream_failed", "AI inference failed. Please try again."))

    return StreamingResponse(gen(), media_type=protocol.NDJSON_MEDIA_TYPE, headers=_STREAM_HEADERS)


_PHOTO_STREAM_DEADLINE_SECONDS = 180


async def _load_meal_dict(db: AsyncSession, user_id: int, meal_id: int) -> dict | None:
    result = await db.execute(
        select(Meal).where(Meal.id == meal_id, Meal.user_id == user_id).options(selectinload(Meal.items))
    )
    meal = result.scalar_one_or_none()
    return _serialize_meal(meal) if meal is not None else None


async def _photo_job_probe(job_id: str, user_id: int) -> tuple[dict | None, dict | None]:
    """Return safe worker diagnostics plus an optional terminal stream event.

    This durable probe makes a dead/misconfigured worker distinguishable from
    a slow AI provider without logging image URLs, prompts, or credentials.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            select(MealAnalysisJob).where(MealAnalysisJob.id == job_id, MealAnalysisJob.user_id == user_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            return None, None
        diagnostics = {
            "status": job.status,
            "attempts": job.attempts,
            "has_celery_task_id": bool(job.celery_task_id),
        }
        if job.status == "completed" and job.meal_id is not None:
            meal_dict = await _load_meal_dict(db, user_id, job.meal_id)
            if meal_dict is not None:
                return diagnostics, protocol.done(meal_dict)
        if job.status == "failed":
            return (
                diagnostics,
                protocol.error(
                    "analysis_failed",
                    "We couldn't analyze this photo. Try again or add a short description.",
                ),
            )
        if job.status == "cancelled":
            return diagnostics, protocol.error("analysis_cancelled", "Photo analysis was cancelled.")
        return diagnostics, None


@router.post("/photo/stream")
async def stream_log_meal_via_photo(
    payload: MealCreatePhoto,
    current_user: User = Depends(get_current_user),
    accept_language: str | None = Header(default="en"),
) -> StreamingResponse:
    """Streams a photo meal analysis as NDJSON while the durable Celery worker
    runs the analysis. The worker publishes partial items to the Redis stream
    bus; this endpoint replays any missed snapshot then tails live events.

    Reconnect/fallback: the existing `POST /photo/analysis` + `GET .../{id}`
    poll pair remains the durable path — a client can always fall back to it,
    and this relay itself consults the DB job row as a safety net.
    """

    async def gen():
        stream_started_at = time.monotonic()
        logger.info(
            "event=meal_analysis_stream_received user_id=%s client_request_id=%s has_hint=%s has_additional_context=%s",
            current_user.id,
            payload.client_request_id,
            bool(payload.text),
            bool(payload.additional_context),
        )
        # Phase 1 — durable job setup (short-lived session).
        async with SessionLocal() as db:
            existing_meal = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
            if existing_meal is not None:
                logger.info(
                    "event=meal_analysis_stream_idempotent_result user_id=%s client_request_id=%s meal_id=%s",
                    current_user.id,
                    payload.client_request_id,
                    existing_meal.id,
                )
                yield protocol.line(protocol.done(_serialize_meal(existing_meal)))
                return

            job = await _find_existing_analysis_by_request_id(db, current_user.id, payload.client_request_id)
            reused_job = job is not None
            if job is None:
                job = MealAnalysisJob(
                    user_id=current_user.id,
                    status="queued",
                    source_type="photo",
                    image_url=payload.image_url,
                    text=payload.text,
                    additional_context=payload.additional_context,
                    meal_category=payload.meal_category,
                    locale=_primary_locale(accept_language),
                    client_request_id=payload.client_request_id,
                )
                db.add(job)
                await db.flush()
                try:
                    from app.tasks.meal_analysis import analyze_photo_meal

                    logger.info(
                        "event=meal_analysis_enqueue_started transport=stream job_id=%s user_id=%s "
                        "client_request_id=%s",
                        job.id,
                        current_user.id,
                        payload.client_request_id,
                    )
                    task = analyze_photo_meal.delay(job.id)
                    job.celery_task_id = task.id
                    await db.commit()
                    logger.info(
                        "event=meal_analysis_enqueue_succeeded transport=stream job_id=%s user_id=%s celery_task_id=%s",
                        job.id,
                        current_user.id,
                        task.id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "event=meal_analysis_queue_unavailable transport=stream job_id=%s user_id=%s "
                        "client_request_id=%s job_status=%s error_type=%s error=%s",
                        job.id,
                        current_user.id,
                        payload.client_request_id,
                        job.status,
                        type(exc).__name__,
                        str(exc),
                    )
                    job.status = "failed"
                    job.error_message = str(exc)
                    await db.commit()
                    logger.error(
                        "event=meal_analysis_queue_failure_persisted transport=stream job_id=%s user_id=%s "
                        "job_status=%s",
                        job.id,
                        current_user.id,
                        job.status,
                    )
                    yield protocol.line(
                        protocol.error("queue_unavailable", "Meal analysis queue is unavailable. Try again shortly.")
                    )
                    return
            job_id = job.id
            job_status = job.status
            job_meal_id = job.meal_id
            job_attempts = job.attempts
            has_celery_task_id = bool(job.celery_task_id)

        logger.info(
            "event=meal_analysis_stream_job_ready job_id=%s user_id=%s client_request_id=%s "
            "status=%s attempts=%s has_celery_task_id=%s reused=%s",
            job_id,
            current_user.id,
            payload.client_request_id,
            job_status,
            job_attempts,
            has_celery_task_id,
            reused_job,
        )

        # Fast path — job already completed (idempotent replay).
        if job_status == "completed" and job_meal_id is not None:
            async with SessionLocal() as db:
                meal_dict = await _load_meal_dict(db, current_user.id, job_meal_id)
            if meal_dict is not None:
                logger.info(
                    "event=meal_analysis_stream_completed_replay job_id=%s meal_id=%s",
                    job_id,
                    job_meal_id,
                )
                yield protocol.line(protocol.done(meal_dict))
                return

        # Phase 2 — relay from the stream bus. Subscribe BEFORE reading the
        # snapshot so nothing published in between is lost; dedupe by item index.
        pubsub = await stream_bus.subscribe(job_id)
        emitted_name = False
        max_index = -1
        heartbeat_count = 0
        outcome = "client_disconnected"
        try:
            snapshot = await stream_bus.read_state(job_id)
            if snapshot:
                if snapshot.get("meal_name"):
                    yield protocol.line(snapshot["meal_name"])
                    emitted_name = True
                for it in snapshot.get("items", []):
                    idx = it.get("index", -1)
                    if idx > max_index:
                        max_index = idx
                        yield protocol.line(it)
                if snapshot.get("terminal"):
                    outcome = f"snapshot_{snapshot['terminal'].get('type', 'terminal')}"
                    yield protocol.line(snapshot["terminal"])
                    return

            deadline = time.monotonic() + _PHOTO_STREAM_DEADLINE_SECONDS
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if msg is None:
                    # No live event — check the durable job row, then heartbeat.
                    heartbeat_count += 1
                    diagnostics, term = await _photo_job_probe(job_id, current_user.id)
                    if heartbeat_count == 1 or heartbeat_count % 3 == 0:
                        log = (
                            logger.warning
                            if diagnostics and diagnostics["status"] == "queued" and diagnostics["attempts"] == 0
                            else logger.info
                        )
                        log(
                            "event=meal_analysis_stream_waiting job_id=%s status=%s attempts=%s "
                            "has_celery_task_id=%s heartbeat=%s elapsed_ms=%s",
                            job_id,
                            diagnostics.get("status") if diagnostics else "missing",
                            diagnostics.get("attempts") if diagnostics else None,
                            diagnostics.get("has_celery_task_id") if diagnostics else None,
                            heartbeat_count,
                            int((time.monotonic() - stream_started_at) * 1000),
                        )
                    if term is not None:
                        outcome = f"database_{term.get('type', 'terminal')}"
                        yield protocol.line(term)
                        return
                    if time.monotonic() > deadline:
                        outcome = "relay_timeout"
                        logger.error(
                            "event=meal_analysis_stream_timeout job_id=%s status=%s attempts=%s elapsed_ms=%s",
                            job_id,
                            diagnostics.get("status") if diagnostics else "missing",
                            diagnostics.get("attempts") if diagnostics else None,
                            int((time.monotonic() - stream_started_at) * 1000),
                        )
                        yield protocol.line(
                            protocol.error("timeout", "Analysis is taking longer than expected. Check back shortly.")
                        )
                        return
                    yield protocol.line(protocol.status("processing"))
                    continue

                try:
                    ev = json.loads(msg["data"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("event=meal_analysis_stream_invalid_bus_event job_id=%s", job_id)
                    continue
                etype = ev.get("type")
                logger.info(
                    "event=meal_analysis_stream_event_received job_id=%s event_type=%s item_index=%s",
                    job_id,
                    etype,
                    ev.get("index"),
                )
                if etype == "meal_name":
                    if not emitted_name:
                        emitted_name = True
                        yield protocol.line(ev)
                elif etype == "item":
                    idx = ev.get("index", -1)
                    if idx > max_index:
                        max_index = idx
                        yield protocol.line(ev)
                elif etype in ("done", "error"):
                    outcome = f"live_{etype}"
                    yield protocol.line(ev)
                    return
                else:
                    yield protocol.line(ev)
        finally:
            try:
                await pubsub.unsubscribe()
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass
            logger.info(
                "event=meal_analysis_stream_closed job_id=%s outcome=%s elapsed_ms=%s",
                job_id,
                outcome,
                int((time.monotonic() - stream_started_at) * 1000),
            )

    return StreamingResponse(gen(), media_type=protocol.NDJSON_MEDIA_TYPE, headers=_STREAM_HEADERS)


@router.get("", response_model=list[MealResponse])
async def list_user_meals(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Meal]:
    """Retrieves a paginated timeline of meals logged by the user."""
    meal_repo = MealRepository(db)

    cutoff = None
    if not await has_premium_access(current_user, db):
        cutoff = dt.datetime.combine(free_history_cutoff(), dt.time.min).replace(tzinfo=dt.UTC)

    return await meal_repo.get_by_user(
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        cutoff_date=cutoff,
    )


@router.get("/date/{date_val}", response_model=list[MealResponse])
async def list_meals_on_date(
    date_val: dt.date,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Meal]:
    """Retrieves all meals logged on a specific calendar date (YYYY-MM-DD)."""
    await ensure_history_date_access(date_val, current_user, db)
    meal_repo = MealRepository(db)
    return await meal_repo.get_user_meals_on_date(user_id=current_user.id, date_val=date_val)


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Uploads a media file and returns a URL usable by AI providers and clients."""
    allowed_types = {
        "application/octet-stream",
        "audio/3gpp",
        "audio/aac",
        "audio/m4a",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/x-m4a",
        "audio/x-wav",
        "audio/webm",
        "image/gif",
        "image/heic",
        "image/heif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
    if file.content_type and file.content_type.lower() not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Choose a supported photo or audio file.",
        )
    if file.size is not None and file.size <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The selected file is empty.",
        )
    if file.size is not None and file.size > settings.MEAL_UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="The selected file is too large.",
        )
    return await save_upload(file, current_user.id)


@router.post("/{id}/refine", response_model=MealResponse)
async def refine_meal_estimate(
    id: int,
    payload: MealRefineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    accept_language: str | None = Header(default="en"),
) -> dict:
    """Conversationally revises an existing AI meal estimate without saving it to the meal."""
    meal_repo = MealRepository(db)
    meal = await meal_repo.get(id)

    if not meal or meal.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal record not found or access forbidden.",
        )

    await ensure_history_date_access(meal.created_at.date(), current_user, db)

    ai_service = AICalorieEstimationService(db)
    user_context = await _build_user_context(
        db,
        current_user,
        _primary_locale(accept_language),
    )
    previous_items = _meal_items_payload(meal)
    user_refinement = payload.user_refinement

    if payload.refinement_type == "voice":
        try:
            transcription = await ai_service.speech_service.transcribe_audio(
                audio_url=payload.user_refinement,
                user_id=current_user.id,
            )
            user_refinement = transcription.transcript
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="I couldn't confidently update this estimate. Try adding a little more detail.",
            )

    try:
        estimation = await ai_service.refine_estimate(
            meal_snapshot=_meal_snapshot(meal),
            user_refinement=user_refinement,
            source_type=meal.source_type,
            user_context=user_context,
            user_id=current_user.id,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="I couldn't confidently update this estimate. Try adding a little more detail.",
        )

    estimation = enforce_estimate_ingredient_invariants(estimation)

    revised_items = [item.model_dump() for item in estimation.items]
    revision = MealRevision(
        meal_id=meal.id,
        user_id=current_user.id,
        refinement_type=payload.refinement_type,
        user_input=user_refinement,
        previous_calories=meal.estimated_calories,
        revised_calories=estimation.estimated_calories,
        calorie_delta=estimation.estimated_calories - meal.estimated_calories,
        previous_items_json=json.dumps(previous_items, ensure_ascii=False),
        revised_items_json=json.dumps(revised_items, ensure_ascii=False),
        ai_summary=estimation.ai_summary,
        model_name=estimation.model_name,
        prompt_version=estimation.prompt_version,
    )
    db.add(revision)
    await db.flush()

    return _meal_response_dict(meal, estimation)


@router.get("/{id}", response_model=MealResponse)
async def get_meal_by_id(
    id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meal:
    """Fetches a specific logged meal by its ID."""
    meal_repo = MealRepository(db)
    meal = await meal_repo.get(id)

    if not meal or meal.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal record not found or access forbidden.",
        )
    await ensure_history_date_access(meal.created_at.date(), current_user, db)
    return meal


@router.patch("/{id}", response_model=MealResponse)
async def update_meal(
    id: int,
    payload: MealUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meal:
    """Updates meal attributes, such as manual calibration/confirmation of calories.

    Triggers a dynamic DailySummary update for the historical date the meal was logged.
    """
    meal_repo = MealRepository(db)
    meal = await meal_repo.get(id)

    if not meal or meal.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal record not found or access forbidden.",
        )

    await ensure_history_date_access(meal.created_at.date(), current_user, db)

    # Perform repository updates. Ingredient quantity/density determine totals.
    try:
        updated_meal = await meal_repo.update(meal, payload)
    except InvalidMealIngredients as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    # When confirmed, learn the ingredient-derived total for future repeat logs.
    if payload.is_confirmed is True:
        try:
            food_memory_repo = FoodMemoryRepository(db)
            await food_memory_repo.upsert_from_meal(
                updated_meal,
                updated_meal.estimated_calories,
            )
        except Exception as e:
            logger.error(f"Failed to update food memory on meal confirmation: {e}")

    # Recalculate summary balance for this specific historical meal date
    try:
        summary_service = SummaryService(db)
        await summary_service.sync_daily_summary(current_user.id, updated_meal.created_at.date())
    except Exception as e:
        logger.error(f"Failed to synchronize daily summary during meal adjustment: {e}")

    return updated_meal


@router.delete("/{id}", status_code=status.HTTP_200_OK)
async def delete_meal(
    id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Deletes a logged meal.

    Immediately updates the caloric DailySummary for the day this meal was deleted from.
    """
    meal_repo = MealRepository(db)
    meal = await meal_repo.get(id)

    if not meal or meal.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal record not found or access forbidden.",
        )

    await ensure_history_date_access(meal.created_at.date(), current_user, db)

    meal_date = meal.created_at.date()

    # Perform the deletion
    await meal_repo.remove(id)

    # Synchronize caloric balance for the day
    try:
        summary_service = SummaryService(db)
        await summary_service.sync_daily_summary(current_user.id, meal_date)
    except Exception as e:
        logger.error(f"Failed to re-sync daily summary after meal deletion: {e}")

    # Commit here so a successful HTTP response means the deletion is durable.
    # Request dependency teardown may otherwise discover a commit failure only
    # after the client has already received a success response.
    try:
        await db.commit()
    except Exception:
        logger.exception("Failed to commit meal deletion: meal_id=%s user_id=%s", id, current_user.id)
        raise

    logger.info("Meal deletion committed: meal_id=%s user_id=%s", id, current_user.id)
    return {"message": "Meal entry successfully removed."}
