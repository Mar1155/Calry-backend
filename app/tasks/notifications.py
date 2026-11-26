import asyncio

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.worker.celery_app import celery_app


@celery_app.task(name="app.tasks.notifications.plan_meal_nudges")
def plan_meal_nudges() -> dict:
    return asyncio.run(_plan_meal_nudges())


async def _plan_meal_nudges() -> dict:
    from app.notifications.dispatcher import build_dispatcher
    from app.notifications.meal_nudges import MealNudgePlanner

    if not (settings.PUSH_NOTIFICATIONS_ENABLED and settings.MEAL_NUDGES_ENABLED):
        return {"status": "disabled"}
    try:
        async with SessionLocal() as db:
            return await MealNudgePlanner(db, build_dispatcher(db)).run()
    finally:
        await engine.dispose()
