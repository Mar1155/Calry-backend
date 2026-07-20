import asyncio
import datetime as dt
import logging
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.ai.errors import AIInvalidResponseError, AIProviderError, ImageAnalysisError
from app.ai.services.calorie_estimation_service import AICalorieEstimationService
from app.core.config import settings
from app.core.exceptions import CalryException
from app.db.session import SessionLocal, engine
from app.models.meal import Meal
from app.models.meal_analysis import MealAnalysisJob
from app.models.user import User
from app.worker.celery_app import celery_app

logger = logging.getLogger("app.tasks.meal_analysis")


@celery_app.task(bind=True, name="app.tasks.meal_analysis.analyze_photo_meal")
def analyze_photo_meal(self, job_id: str) -> int | None:
    started_at = time.perf_counter()
    task_id = self.request.id
    retry_number = self.request.retries
    logger.info(
        "event=meal_analysis_worker_task_received job_id=%s celery_task_id=%s retry=%s",
        job_id,
        task_id,
        retry_number,
    )
    try:
        meal_id = asyncio.run(_run_photo_analysis_with_cleanup(job_id))
        logger.info(
            "event=meal_analysis_worker_task_finished job_id=%s celery_task_id=%s meal_id=%s duration_ms=%s",
            job_id,
            task_id,
            meal_id,
            int((time.perf_counter() - started_at) * 1000),
        )
        return meal_id
    except Exception as exc:
        retryable = _is_retryable_failure(exc)
        logger.exception(
            "event=meal_analysis_worker_task_failed job_id=%s celery_task_id=%s retry=%s "
            "retryable=%s error_type=%s duration_ms=%s",
            job_id,
            task_id,
            retry_number,
            retryable,
            type(exc).__name__,
            int((time.perf_counter() - started_at) * 1000),
        )
        if retryable and retry_number < settings.MEAL_ANALYSIS_MAX_RETRIES:
            asyncio.run(_mark_job_queued(job_id, str(exc)))
            countdown = min(60, 5 * (2**retry_number))
            logger.warning(
                "event=meal_analysis_worker_retry_scheduled job_id=%s celery_task_id=%s "
                "next_retry=%s countdown_seconds=%s",
                job_id,
                task_id,
                retry_number + 1,
                countdown,
            )
            raise self.retry(exc=exc, countdown=countdown)
        asyncio.run(_mark_job_failed(job_id, str(exc)))
        logger.error(
            "event=meal_analysis_worker_failed_permanently job_id=%s celery_task_id=%s retries=%s",
            job_id,
            task_id,
            retry_number,
        )
        raise


def _is_retryable_failure(exc: Exception) -> bool:
    """Retry transient transport/provider/DB failures, not bad user input or
    malformed AI output. Unknown infrastructure failures get a bounded retry."""
    if isinstance(exc, AIInvalidResponseError):
        return False
    if isinstance(exc, CalryException):
        explicit = exc.details.get("retryable")
        if isinstance(explicit, bool):
            return explicit
        if isinstance(exc, (ImageAnalysisError, AIProviderError)):
            return False
    if isinstance(exc, (FileNotFoundError, TypeError, ValueError)):
        return False
    if isinstance(exc, IntegrityError):
        return True
    return True


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
            logger.warning("event=meal_analysis_worker_job_missing job_id=%s", job_id)
            return None
        logger.info(
            "event=meal_analysis_worker_job_loaded job_id=%s user_id=%s client_request_id=%s "
            "status=%s attempts=%s has_celery_task_id=%s",
            job.id,
            job.user_id,
            job.client_request_id,
            job.status,
            job.attempts,
            bool(job.celery_task_id),
        )
        if job.status == "cancelled":
            logger.info("event=meal_analysis_worker_job_skipped job_id=%s reason=cancelled", job.id)
            return None
        if job.status == "completed":
            # Late subscriber: re-publish the terminal `done` from the persisted meal.
            if job.meal_id is not None:
                meal_dict = await _serialize_meal_for_stream(db, job.meal_id)
                if meal_dict is not None:
                    await stream_bus.publish(job_id, protocol.done(meal_dict))
            logger.info(
                "event=meal_analysis_worker_job_replayed job_id=%s meal_id=%s",
                job.id,
                job.meal_id,
            )
            return job.meal_id

        user = await db.get(User, job.user_id)
        if user is None:
            raise RuntimeError("Analysis user not found")

        job.status = "processing"
        job.attempts += 1
        job.error_message = None
        await db.commit()
        logger.info(
            "event=meal_analysis_worker_processing job_id=%s user_id=%s attempt=%s",
            job.id,
            job.user_id,
            job.attempts,
        )

        existing_meal = await _find_existing_by_request_id(db, job.user_id, job.client_request_id)
        if existing_meal is not None:
            job.status = "completed"
            job.meal_id = existing_meal.id
            job.completed_at = dt.datetime.now(dt.UTC)
            await db.commit()
            meal_dict = await _serialize_meal_for_stream(db, existing_meal.id)
            if meal_dict is not None:
                await stream_bus.publish(job_id, protocol.done(meal_dict))
            logger.info(
                "event=meal_analysis_worker_idempotent_result job_id=%s meal_id=%s",
                job.id,
                existing_meal.id,
            )
            return existing_meal.id

        ai_service = AICalorieEstimationService(db)
        user_context = await _build_user_context(db, user, job.locale or "en")

        await stream_bus.publish(job_id, protocol.status("processing"))
        analysis_started_at = time.perf_counter()
        preview_count = 0
        logger.info(
            "event=meal_analysis_worker_ai_started job_id=%s user_id=%s model=%s",
            job.id,
            user.id,
            settings.OPENROUTER_IMAGE_MODEL,
        )
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
                logger.info(
                    "event=meal_analysis_worker_ai_completed job_id=%s preview_count=%s item_count=%s duration_ms=%s",
                    job.id,
                    preview_count,
                    len(estimation.items),
                    int((time.perf_counter() - analysis_started_at) * 1000),
                )
            else:
                preview_count += 1
                logger.info(
                    "event=meal_analysis_worker_preview job_id=%s event_type=%s item_index=%s preview_count=%s",
                    job.id,
                    ev.get("type"),
                    ev.get("index"),
                    preview_count,
                )
                await stream_bus.publish(job_id, ev)

            # Cancellation normally terminates this Celery task immediately.
            # This durable check also covers workers/pools that cannot terminate
            # a running task and the race where cancellation arrives at the end.
            await db.refresh(job, attribute_names=["status"])
            if job.status == "cancelled":
                logger.info("event=meal_analysis_worker_cancelled job_id=%s phase=ai_stream", job.id)
                return None

        if estimation is None:
            logger.error("event=meal_analysis_worker_missing_result job_id=%s", job.id)
            raise RuntimeError("stream produced no result")

        await db.refresh(job, attribute_names=["status"])
        if job.status == "cancelled":
            logger.info("event=meal_analysis_worker_cancelled job_id=%s phase=before_persistence", job.id)
            return None

        raw_desc = estimation.meal_name.strip() or (job.text or "Meal photo")
        logger.info(
            "event=meal_analysis_worker_persistence_started job_id=%s item_count=%s",
            job.id,
            len(estimation.items),
        )
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

        # Cancellation may land while _process_and_save_meal is flushing and
        # synchronizing the daily summary. Never overwrite cancelled with
        # completed or leave the just-created unconfirmed meal behind.
        await db.refresh(job, attribute_names=["status"])
        if job.status == "cancelled":
            meal_date = meal.created_at.date()
            await db.delete(meal)
            await db.flush()
            from app.insights.versioning import DomainEvent, InsightVersionService
            from app.services.summary import SummaryService

            await SummaryService(db).sync_daily_summary(user.id, meal_date)
            await InsightVersionService(db).record(
                user.id,
                DomainEvent.MEAL_DELETED,
                affected_date=meal_date,
            )
            await db.commit()
            logger.info("event=meal_analysis_worker_cancelled job_id=%s phase=after_persistence", job.id)
            return None

        job.status = "completed"
        job.meal_id = meal.id
        job.completed_at = dt.datetime.now(dt.UTC)
        await db.commit()
        logger.info(
            "event=meal_analysis_worker_persisted job_id=%s meal_id=%s",
            job.id,
            meal.id,
        )

        meal_dict = await _serialize_meal_for_stream(db, meal.id)
        if meal_dict is not None:
            await stream_bus.publish(job_id, protocol.done(meal_dict))
            logger.info(
                "event=meal_analysis_worker_terminal_published job_id=%s meal_id=%s terminal=done",
                job.id,
                meal.id,
            )
        return meal.id


async def _get_job(db, job_id: str) -> MealAnalysisJob | None:
    result = await db.execute(select(MealAnalysisJob).where(MealAnalysisJob.id == job_id))
    return result.scalar_one_or_none()


async def _mark_job_queued(job_id: str, error: str) -> None:
    try:
        async with SessionLocal() as db:
            job = await _get_job(db, job_id)
            if job is None or job.status in {"completed", "cancelled"}:
                return
            job.status = "queued"
            job.error_message = error[:2000]
            await db.commit()
            logger.warning(
                "event=meal_analysis_worker_retry_persisted job_id=%s status=queued attempts=%s",
                job.id,
                job.attempts,
            )
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
            if job is None or job.status in {"completed", "cancelled"}:
                return

            existing_meal = await _find_existing_meal(db, job.user_id, job.client_request_id)
            if existing_meal is not None:
                job.status = "completed"
                job.meal_id = existing_meal.id
                job.completed_at = dt.datetime.now(dt.UTC)
                terminal_event = protocol.done(MealResponse.model_validate(existing_meal).model_dump(mode="json"))
            else:
                job.status = "failed"
                job.error_message = error[:2000]
                terminal_event = protocol.error("analysis_failed", "Photo analysis failed. Please try again.")
            await db.commit()

            logger.error(
                "event=meal_analysis_worker_failure_persisted job_id=%s status=%s attempts=%s terminal=%s",
                job.id,
                job.status,
                job.attempts,
                terminal_event.get("type") if terminal_event else None,
            )

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
