"""Celery tasks for the AI Memory System.

Distillation is event-driven (a meal is confirmed/corrected) and consolidation
runs nightly. Both are pure-Python and LLM-free. The enqueue helper degrades to a
no-op when async distillation is disabled or no broker is reachable, so the meal
path never fails because of Memory.
"""

import asyncio
import logging

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.worker.celery_app import celery_app

logger = logging.getLogger("app.tasks.memory")


def enqueue_memory_distillation(user_id: int) -> None:
    """Best-effort trigger from the request path. Never raises."""
    if not settings.MEMORY_ENABLED or not settings.MEMORY_DISTILLATION_ASYNC_ENABLED:
        return
    try:
        distill_user_memory.delay(user_id)
    except Exception as exc:  # broker unreachable etc. — non-fatal
        logger.warning("event=memory_distillation_enqueue_failed user_id=%s error=%s", user_id, exc)


@celery_app.task(bind=True, name="app.tasks.memory.distill_user_memory")
def distill_user_memory(self, user_id: int) -> dict:
    logger.info("event=memory_distillation_received user_id=%s", user_id)
    try:
        return asyncio.run(_distill_with_cleanup(user_id))
    except Exception:
        logger.exception("event=memory_distillation_failed user_id=%s", user_id)
        raise


@celery_app.task(bind=True, name="app.tasks.memory.consolidate_memory")
def consolidate_memory(self) -> dict:
    logger.info("event=memory_consolidation_started")
    try:
        return asyncio.run(_consolidate_with_cleanup())
    except Exception:
        logger.exception("event=memory_consolidation_failed")
        raise


async def _distill_with_cleanup(user_id: int) -> dict:
    from app.memory.service import MemoryService

    try:
        async with SessionLocal() as db:
            result = await MemoryService(db).distill_user(user_id)
            await db.commit()
            logger.info("event=memory_distillation_done user_id=%s result=%s", user_id, result)
            return result
    finally:
        await engine.dispose()


async def _consolidate_with_cleanup() -> dict:
    from app.memory.service import MemoryService

    try:
        async with SessionLocal() as db:
            service = MemoryService(db)
            user_ids = await service.repo.list_users_with_beliefs()
            transitioned = 0
            for user_id in user_ids:
                outcome = await service.consolidate_user(user_id)
                transitioned += outcome.get("transitioned", 0)
            await db.commit()
            result = {"users": len(user_ids), "transitioned": transitioned}
            logger.info("event=memory_consolidation_done result=%s", result)
            return result
    finally:
        await engine.dispose()
