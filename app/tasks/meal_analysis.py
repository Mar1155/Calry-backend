import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai.services.calorie_estimation_service import AICalorieEstimationService
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.meal import Meal
from app.models.meal_analysis import MealAnalysisJob
from app.models.user import User
from app.worker.celery_app import celery_app

logger = logging.getLogger("app.tasks.meal_analysis")


@celery_app.task(bind=True, name="app.tasks.meal_analysis.analyze_photo_meal")
def analyze_photo_meal(self, job_id: str) -> int | None:
    try:
        return asyncio.run(_run_photo_analysis_with_cleanup(job_id))
    except Exception as exc:
        if self.request.retries < settings.MEAL_ANALYSIS_MAX_RETRIES:
            asyncio.run(_mark_job_queued(job_id, str(exc)))
            raise self.retry(exc=exc, countdown=min(60, 5 * (2**self.request.retries)))
        asyncio.run(_mark_job_failed(job_id, str(exc)))
        logger.exception("Meal analysis job failed permanently: %s", job_id)
        raise


async def _run_photo_analysis_with_cleanup(job_id: str) -> int | None:
    try:
        return await _run_photo_analysis(job_id)
    finally:
        from app.ai.providers.openrouter import close_shared_client

        await close_shared_client()


async def _run_photo_analysis(job_id: str) -> int | None:
    from app.api.v1.routes.meals import _build_user_context, _find_existing_by_request_id, _process_and_save_meal

    async with SessionLocal() as db:
        job = await _get_job(db, job_id)
        if job is None:
            return None
        if job.status == "completed":
            return job.meal_id

        user = await db.get(User, job.user_id)
        if user is None:
            raise RuntimeError("Analysis user not found")

        job.status = "processing"
        job.attempts += 1
        job.error_message = None
        await db.commit()

        existing_meal = await _find_existing_by_request_id(db, job.user_id, job.client_request_id)
        if existing_meal is not None:
            job.status = "completed"
            job.meal_id = existing_meal.id
            job.completed_at = dt.datetime.now(dt.UTC)
            await db.commit()
            return existing_meal.id

        ai_service = AICalorieEstimationService(db)
        user_context = await _build_user_context(db, user, job.locale or "en")
        estimation = await ai_service.estimate_from_image(
            image_url=job.image_url,
            optional_hint=job.text,
            user_context=user_context,
            user_id=user.id,
            additional_context=job.additional_context,
        )

        raw_desc = estimation.meal_name.strip() or (job.text or "Meal photo")
        meal = await _process_and_save_meal(
            db=db,
            user=user,
            source_type="photo",
            original_input=raw_desc,
            image_url=job.image_url,
            audio_url=None,
            estimation=estimation,
            client_request_id=job.client_request_id,
        )

        job.status = "completed"
        job.meal_id = meal.id
        job.completed_at = dt.datetime.now(dt.UTC)
        await db.commit()
        return meal.id


async def _get_job(db, job_id: str) -> MealAnalysisJob | None:
    result = await db.execute(select(MealAnalysisJob).where(MealAnalysisJob.id == job_id))
    return result.scalar_one_or_none()


async def _mark_job_queued(job_id: str, error: str) -> None:
    async with SessionLocal() as db:
        job = await _get_job(db, job_id)
        if job is None or job.status == "completed":
            return
        job.status = "queued"
        job.error_message = error[:2000]
        await db.commit()


async def _mark_job_failed(job_id: str, error: str) -> None:
    async with SessionLocal() as db:
        job = await _get_job(db, job_id)
        if job is None or job.status == "completed":
            return

        existing_meal = await _find_existing_meal(db, job.user_id, job.client_request_id)
        if existing_meal is not None:
            job.status = "completed"
            job.meal_id = existing_meal.id
            job.completed_at = dt.datetime.now(dt.UTC)
        else:
            job.status = "failed"
            job.error_message = error[:2000]
        await db.commit()


async def _find_existing_meal(db, user_id: int, client_request_id: str | None) -> Meal | None:
    if not client_request_id:
        return None
    result = await db.execute(
        select(Meal)
        .where(Meal.user_id == user_id, Meal.client_request_id == client_request_id)
        .options(selectinload(Meal.items))
    )
    return result.scalar_one_or_none()
