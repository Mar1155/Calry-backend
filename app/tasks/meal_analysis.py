import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai.services.calorie_estimation_service import AICalorieEstimationService
from app.core.config import settings
from app.db.session import SessionLocal, engine
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
        from app.services.stream_bus import close_redis

        await close_shared_client()
        await close_redis()
        # asyncio.run() opens/closes a fresh event loop per task, but `engine`'s
        # pooled connections are bound to whichever loop created them. Dispose
        # here (same loop) so the next asyncio.run() call starts a clean pool
        # instead of reusing connections tied to an already-closed loop.
        await engine.dispose()


async def _serialize_meal_for_stream(db, meal_id: int) -> dict | None:
    from app.schemas.meal import MealResponse

    meal = await _load_meal(db, meal_id)
    if meal is None:
        return None
    return MealResponse.model_validate(meal).model_dump(mode="json")


async def _load_meal(db, meal_id: int) -> Meal | None:
    result = await db.execute(select(Meal).where(Meal.id == meal_id).options(selectinload(Meal.items)))
    return result.scalar_one_or_none()


async def _run_photo_analysis(job_id: str) -> int | None:
    """Durable photo analysis. Runs the streaming estimator so partial items can
    be relayed live to a `/photo/stream` client via the Redis stream bus, while
    still persisting the meal + job row exactly as the poll path expects.

    Preview events are published best-effort; `done` is published only after the
    meal is committed. A retryable failure re-raises WITHOUT publishing an error
    (the outer task retries and re-streams); a permanent failure publishes the
    error from `_mark_job_failed`.
    """
    from app.ai.streaming import protocol
    from app.api.v1.routes.meals import _build_user_context, _find_existing_by_request_id, _process_and_save_meal
    from app.services import stream_bus

    async with SessionLocal() as db:
        job = await _get_job(db, job_id)
        if job is None:
            return None
        if job.status == "completed":
            # Late subscriber: re-publish the terminal `done` from the persisted meal.
            if job.meal_id is not None:
                meal_dict = await _serialize_meal_for_stream(db, job.meal_id)
                if meal_dict is not None:
                    await stream_bus.publish(job_id, protocol.done(meal_dict))
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
            meal_dict = await _serialize_meal_for_stream(db, existing_meal.id)
            if meal_dict is not None:
                await stream_bus.publish(job_id, protocol.done(meal_dict))
            return existing_meal.id

        ai_service = AICalorieEstimationService(db)
        user_context = await _build_user_context(db, user, job.locale or "en")

        await stream_bus.publish(job_id, protocol.status("processing"))
        estimation = None
        async for ev in ai_service.stream_estimate_from_image(
            job.image_url,
            user_context=user_context,
            optional_hint=job.text,
            user_id=user.id,
            additional_context=job.additional_context,
        ):
            if ev.get("type") == "__complete__":
                estimation = ev["result"]
            else:
                await stream_bus.publish(job_id, ev)

        if estimation is None:
            raise RuntimeError("stream produced no result")

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
            meal_category=job.meal_category,
        )

        job.status = "completed"
        job.meal_id = meal.id
        job.completed_at = dt.datetime.now(dt.UTC)
        await db.commit()

        meal_dict = await _serialize_meal_for_stream(db, meal.id)
        if meal_dict is not None:
            await stream_bus.publish(job_id, protocol.done(meal_dict))
        return meal.id


async def _get_job(db, job_id: str) -> MealAnalysisJob | None:
    result = await db.execute(select(MealAnalysisJob).where(MealAnalysisJob.id == job_id))
    return result.scalar_one_or_none()


async def _mark_job_queued(job_id: str, error: str) -> None:
    try:
        async with SessionLocal() as db:
            job = await _get_job(db, job_id)
            if job is None or job.status == "completed":
                return
            job.status = "queued"
            job.error_message = error[:2000]
            await db.commit()
    finally:
        await engine.dispose()


async def _mark_job_failed(job_id: str, error: str) -> None:
    from app.ai.streaming import protocol
    from app.schemas.meal import MealResponse
    from app.services import stream_bus

    terminal_event: dict | None = None
    try:
        async with SessionLocal() as db:
            job = await _get_job(db, job_id)
            if job is None or job.status == "completed":
                return

            existing_meal = await _find_existing_meal(db, job.user_id, job.client_request_id)
            if existing_meal is not None:
                job.status = "completed"
                job.meal_id = existing_meal.id
                job.completed_at = dt.datetime.now(dt.UTC)
                terminal_event = protocol.done(
                    MealResponse.model_validate(existing_meal).model_dump(mode="json")
                )
            else:
                job.status = "failed"
                job.error_message = error[:2000]
                terminal_event = protocol.error("analysis_failed", "Photo analysis failed. Please try again.")
            await db.commit()

        if terminal_event is not None:
            await stream_bus.publish(job_id, terminal_event)
    finally:
        await stream_bus.close_redis()
        await engine.dispose()


async def _find_existing_meal(db, user_id: int, client_request_id: str | None) -> Meal | None:
    if not client_request_id:
        return None
    result = await db.execute(
        select(Meal)
        .where(Meal.user_id == user_id, Meal.client_request_id == client_request_id)
        .options(selectinload(Meal.items))
    )
    return result.scalar_one_or_none()
