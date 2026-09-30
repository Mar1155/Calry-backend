"""Celery task for ingredient image generation (C29).

Runs after a meal is saved, never as part of the request/response path. One
illustration is generated (or reused) per canonical ingredient name and
written back onto that meal's items. Best-effort throughout: the enqueue
helper degrades to a no-op when disabled or no broker is reachable, and the
task itself never lets one ingredient's failure stop the others.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai.services.ingredient_image_service import IngredientImageService
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.meal import Meal
from app.worker.celery_app import celery_app

logger = logging.getLogger("app.tasks.ingredient_images")


def enqueue_ingredient_images(meal_id: int, *, user_id: int | None = None) -> None:
    """Best-effort trigger from the request path. Never raises."""
    if not settings.INGREDIENT_IMAGE_ENABLED:
        return
    try:
        generate_ingredient_images.delay(meal_id, user_id)
    except Exception as exc:  # broker unreachable etc. — non-fatal
        logger.warning("event=ingredient_image_enqueue_failed meal_id=%s error=%s", meal_id, exc)


@celery_app.task(bind=True, name="app.tasks.ingredient_images.generate_ingredient_images", max_retries=1)
def generate_ingredient_images(self, meal_id: int, user_id: int | None = None) -> None:
    logger.info("event=ingredient_image_task_received meal_id=%s", meal_id)
    try:
        asyncio.run(_generate_for_meal_with_cleanup(meal_id, user_id))
    except Exception:
        logger.exception("event=ingredient_image_task_failed meal_id=%s", meal_id)
        # The meal itself is unaffected either way; a retry only guards
        # against the meal row not being visible yet (see _process_and_save_meal).
        raise self.retry(countdown=5)


async def _generate_for_meal_with_cleanup(meal_id: int, user_id: int | None) -> None:
    """Mirrors _run_photo_analysis_with_cleanup (app.tasks.meal_analysis): each
    Celery task invocation opens and closes its own event loop via
    asyncio.run(). The shared OpenRouter httpx client and the SQLAlchemy
    engine's pooled connections are lazily created on first use and bound to
    whatever loop was current at that moment — reused as-is from a later
    invocation, a request against either fails with "Event loop is closed"
    (observed live in staging on 2026-09-30: the first ingredient of a meal
    intermittently failed this way while the rest of the same run succeeded).
    Tearing both down here forces the next invocation to create fresh ones on
    its own live loop, whichever task that turns out to be."""
    try:
        await _generate_for_meal(meal_id, user_id)
    finally:
        from app.ai.providers.openrouter import close_shared_client

        await close_shared_client()
        await engine.dispose()


async def _generate_for_meal(meal_id: int, user_id: int | None) -> None:
    async with SessionLocal() as db:
        meal = await db.scalar(
            select(Meal).where(Meal.id == meal_id).options(selectinload(Meal.items))
        )
        if meal is None:
            # Retryable: the API's commit may not have landed yet.
            raise LookupError(f"meal {meal_id} not found")

        service = IngredientImageService(db)
        for item in meal.items:
            if item.image_url:
                continue
            try:
                item.image_url = await service.get_or_generate(item.name, user_id=user_id)
            except Exception:  # noqa: BLE001 — one bad ingredient never stops the rest
                logger.exception(
                    "event=ingredient_image_item_failed meal_id=%s item_id=%s", meal_id, item.id
                )
        await db.commit()
        logger.info(
            "event=ingredient_image_task_completed meal_id=%s items=%s with_image=%s",
            meal_id,
            len(meal.items),
            sum(1 for item in meal.items if item.image_url),
        )
