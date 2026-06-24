import datetime as dt
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
from app.models.meal import Meal, MealItem
from app.models.user import User
from app.repositories.food_memory import FoodMemoryRepository
from app.repositories.meal import MealRepository
from app.schemas.meal import (
    MealCreatePhoto,
    MealCreateText,
    MealCreateVoice,
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
        needs_clarification=estimation.needs_clarification,
        clarifying_question=estimation.clarifying_question,
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
    """Retrieves calibration/correction history and profile to build a comprehensive UserContext."""
    from app.ai.services.correction_context_service import AICorrectionContextService

    correction_service = AICorrectionContextService(db)
    summary = await correction_service.get_user_correction_summary(user.id)
    avg_pct = await correction_service.get_average_correction_percent(user.id)

    return UserContext(
        daily_calorie_goal=user.daily_calorie_goal,
        locale=None,
        timezone=None,
        previous_corrections_summary=summary,
        sex=user.sex,
        age=user.age,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        goal_type=user.goal_type,
        avg_correction_percent=avg_pct,
    )


@router.post("/text", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_meal_via_text(
    payload: MealCreateText,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meal:
    """Logs a meal by parsing free-form written text."""
    ai_service = AICalorieEstimationService(db)

    # 1. Build user context
    user_context = await _build_user_context(db, current_user)

    # 2. Fetch calorie estimation from AI Orchestrator
    estimation = await ai_service.estimate_from_text(
        text=payload.text,
        user_context=user_context,
        user_id=current_user.id,
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
    )
    return meal


@router.post("/photo", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_meal_via_photo(
    payload: MealCreatePhoto,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meal:
    """Logs a meal by analyzing an image URL with optional text description."""
    ai_service = AICalorieEstimationService(db)

    # 1. Build user context
    user_context = await _build_user_context(db, current_user)

    # 2. Fetch calorie estimation using Multimodal Vision AI
    estimation = await ai_service.estimate_from_image(
        image_url=payload.image_url,
        optional_hint=payload.text,
        user_context=user_context,
        user_id=current_user.id,
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
    )
    return meal


@router.post("/voice", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def log_meal_via_voice(
    payload: MealCreateVoice,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meal:
    """Logs a meal by transcribing a recorded audio file URL and parsing the text."""
    ai_service = AICalorieEstimationService(db)

    # 1. Build user context
    user_context = await _build_user_context(db, current_user)

    # 2. Transcribe voice note and estimate caloric components
    transcript, estimation = await ai_service.estimate_from_voice(
        audio_url=payload.audio_url,
        user_context=user_context,
        user_id=current_user.id,
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
