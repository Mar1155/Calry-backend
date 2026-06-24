import datetime as dt
import json
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas.meal_estimate import MealEstimateResult, UserContext
from app.ai.services.calorie_estimation_service import AICalorieEstimationService
from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.meal import Meal, MealItem, MealRevision
from app.models.user import User
from app.repositories.food_memory import FoodMemoryRepository
from app.repositories.meal import MealRepository
from app.schemas.meal import (
    MealCreatePhoto,
    MealCreateText,
    MealCreateVoice,
    MealRefineRequest,
    MealResponse,
    MealUpdate,
)
from app.services.summary import SummaryService

logger = logging.getLogger("app.api.meals")
router = APIRouter()


async def _process_and_save_meal(
    db: AsyncSession,
    user: User,
    source_type: str,
    original_input: str,
    image_url: str | None,
    audio_url: str | None,
    estimation: MealEstimateResult,
    client_request_id: str | None = None,
) -> Meal:
    """Helper method to construct a Meal record with children and trigger summary sync."""
    meal_repo = MealRepository(db)

    # 1. Instantiate Core Meal entity with new AI pipeline fields
    meal = Meal(
        user_id=user.id,
        source_type=source_type,
        original_input=original_input,
        image_url=image_url,
        audio_url=audio_url,
        meal_name=estimation.meal_name,
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
        summary_service = SummaryService(db)
        meal_date = meal.created_at.date()
        await summary_service.sync_daily_summary(user.id, meal_date)
    except Exception as e:
        logger.error(f"Failed to synchronize daily summary on meal log: {e}")

    # 4. Asynchronously retrieve the completed Meal with preloaded items to prevent lazyloading issues
    from sqlalchemy.future import select
    from sqlalchemy.orm import selectinload

    stmt = select(Meal).where(Meal.id == meal.id).options(selectinload(Meal.items))
    result = await db.execute(stmt)
    return result.scalar_one()


async def _build_user_context(db: AsyncSession, user: User) -> UserContext:
    """Builds UserContext from a SINGLE correction-history query (C5): the prose
    summary for the prompt and the deterministic per-source bias for C11."""
    from app.ai.services.correction_context_service import AICorrectionContextService

    correction_service = AICorrectionContextService(db)
    context = await correction_service.get_correction_context(user.id)

    return UserContext(
        daily_calorie_goal=user.daily_calorie_goal,
        locale=None,
        timezone=None,
        previous_corrections_summary=context.summary,
        sex=user.sex,
        age=user.age,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        goal_type=user.goal_type,
        correction_bias_by_source=context.bias_by_source or None,
    )


async def _find_existing_by_request_id(
    db: AsyncSession, user_id: int, client_request_id: str | None
) -> Meal | None:
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


@router.post("/text", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_meal_via_text(
    payload: MealCreateText,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meal:
    """Logs a meal by parsing free-form written text."""
    # 0. Idempotency: return the existing meal on a repeat request id.
    existing = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
    if existing is not None:
        return existing

    ai_service = AICalorieEstimationService(db)

    # 1. Build user context
    user_context = await _build_user_context(db, current_user)

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
    )
    return meal


@router.post("/photo", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_meal_via_photo(
    payload: MealCreatePhoto,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meal:
    """Logs a meal by analyzing an image URL with optional text description."""
    # 0. Idempotency: return the existing meal on a repeat request id.
    existing = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
    if existing is not None:
        return existing

    ai_service = AICalorieEstimationService(db)

    # 1. Build user context
    user_context = await _build_user_context(db, current_user)

    # 2. Fetch calorie estimation using Multimodal Vision AI
    estimation = await ai_service.estimate_from_image(
        image_url=payload.image_url,
        optional_hint=payload.text,
        user_context=user_context,
        user_id=current_user.id,
        additional_context=payload.additional_context,
    )

    # 3. Build and commit entities
    raw_desc = payload.text if payload.text else "Multimodal image scan"
    meal = await _process_and_save_meal(
        db=db,
        user=current_user,
        source_type="photo",
        original_input=raw_desc,
        image_url=payload.image_url,
        audio_url=None,
        estimation=estimation,
        client_request_id=payload.client_request_id,
    )
    return meal


@router.post("/voice", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_meal_via_voice(
    payload: MealCreateVoice,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meal:
    """Logs a meal by transcribing a recorded audio file URL and parsing the text."""
    # 0. Idempotency: return the existing meal on a repeat request id.
    existing = await _find_existing_by_request_id(db, current_user.id, payload.client_request_id)
    if existing is not None:
        return existing

    ai_service = AICalorieEstimationService(db)

    # 1. Build user context
    user_context = await _build_user_context(db, current_user)

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
    )
    return meal


@router.get("", response_model=list[MealResponse])
async def list_user_meals(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Meal]:
    """Retrieves a paginated timeline of meals logged by the user."""
    meal_repo = MealRepository(db)

    return await meal_repo.get_by_user(
        user_id=current_user.id, skip=skip, limit=limit
    )


@router.get("/date/{date_val}", response_model=list[MealResponse])
async def list_meals_on_date(
    date_val: dt.date,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Meal]:
    """Retrieves all meals logged on a specific calendar date (YYYY-MM-DD)."""
    meal_repo = MealRepository(db)
    return await meal_repo.get_user_meals_on_date(user_id=current_user.id, date_val=date_val)


@router.post("/{id}/refine", response_model=MealResponse)
async def refine_meal_estimate(
    id: int,
    payload: MealRefineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Conversationally revises an existing AI meal estimate without saving it to the meal."""
    meal_repo = MealRepository(db)
    meal = await meal_repo.get(id)

    if not meal or meal.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal record not found or access forbidden.",
        )

    ai_service = AICalorieEstimationService(db)
    user_context = await _build_user_context(db, current_user)
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

    # Perform repository updates
    updated_meal = await meal_repo.update(meal, payload)

    # When user confirms calories, learn from the correction for future repeat logs
    if payload.confirmed_calories is not None:
        try:
            food_memory_repo = FoodMemoryRepository(db)
            await food_memory_repo.upsert_from_meal(updated_meal, payload.confirmed_calories)
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

    meal_date = meal.created_at.date()

    # Perform the deletion
    await meal_repo.remove(id)

    # Synchronize caloric balance for the day
    try:
        summary_service = SummaryService(db)
        await summary_service.sync_daily_summary(current_user.id, meal_date)
    except Exception as e:
        logger.error(f"Failed to re-sync daily summary after meal deletion: {e}")

    return {"message": "Meal entry successfully removed."}


UPLOAD_DIR = Path("app/static/uploads")

@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Uploads a local media file (image/audio) and returns an accessible path.

    Useful for offline/local development or setups without Firebase Storage.
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix if file.filename else ""
    if not ext:
        if "image" in (file.content_type or ""):
            ext = ".jpg"
        elif "audio" in (file.content_type or ""):
            ext = ".mp3"
        else:
            ext = ".bin"

    unique_filename = f"{uuid.uuid4()}{ext}"
    dest_path = UPLOAD_DIR / unique_filename

    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"url": f"/static/uploads/{unique_filename}"}
