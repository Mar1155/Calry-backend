import asyncio
import datetime as dt
import logging

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.insight import InsightNotificationDelivery, ProactiveInsightEvent
from app.worker.celery_app import celery_app

logger = logging.getLogger("app.tasks.proactive_insights")


def enqueue_proactive_processing(event_id: str) -> None:
    if not settings.ENABLE_PROACTIVE_INSIGHTS or not settings.PROACTIVE_INSIGHTS_ASYNC_ENABLED or settings.is_testing:
        return
    try:
        process_proactive_event.apply_async(args=[event_id], countdown=2)
    except Exception as exc:
        logger.warning(
            "event=proactive_insight_enqueue_failed event_id=%s error=%s",
            event_id,
            type(exc).__name__,
        )


@celery_app.task(name="app.tasks.proactive_insights.process_event")
def process_proactive_event(event_id: str) -> dict:
    return asyncio.run(_process_event(event_id))


@celery_app.task(name="app.tasks.proactive_insights.sweep_pending")
def sweep_pending_proactive_events() -> dict:
    return asyncio.run(_sweep_pending())


@celery_app.task(name="app.tasks.proactive_insights.evaluate_periodic")
def evaluate_periodic_proactive_insights(period: str) -> dict:
    return asyncio.run(_evaluate_periodic(period))


@celery_app.task(name="app.tasks.proactive_insights.evaluate_due")
def evaluate_due_proactive_insights() -> dict:
    return asyncio.run(_evaluate_due())


@celery_app.task(name="app.tasks.proactive_insights.deliver_notification")
def deliver_proactive_notification(delivery_id: int) -> dict:
    return asyncio.run(_deliver_notification(delivery_id))


@celery_app.task(name="app.tasks.proactive_insights.sweep_notifications")
def sweep_proactive_notifications() -> dict:
    return asyncio.run(_sweep_notifications())


async def _process_event(event_id: str) -> dict:
    from app.proactive_insights.service import ProactiveInsightService

    if not settings.ENABLE_PROACTIVE_INSIGHTS:
        return {"status": "disabled", "persisted": 0}
    try:
        async with SessionLocal() as db:
            result = await ProactiveInsightService(db).process_event(event_id)
            await db.commit()
            return result
    finally:
        await engine.dispose()


async def _sweep_pending() -> dict:
    if not settings.ENABLE_PROACTIVE_INSIGHTS:
        return {"status": "disabled", "events": 0, "persisted": 0}
    try:
        async with SessionLocal() as db:
            event_ids = list(
                (
                    await db.scalars(
                        select(ProactiveInsightEvent.event_id)
                        .where(
                            ProactiveInsightEvent.status.in_(("pending", "failed")),
                            ProactiveInsightEvent.attempts < 3,
                        )
                        .order_by(ProactiveInsightEvent.created_at)
                        .limit(100)
                    )
                ).all()
            )
            await db.commit()
        for event_id in event_ids:
            enqueue_proactive_processing(event_id)
        return {"events": len(event_ids), "enqueued": len(event_ids)}
    finally:
        await engine.dispose()


async def _evaluate_periodic(period: str) -> dict:
    from app.proactive_insights.service import ProactiveInsightService

    if not settings.ENABLE_PROACTIVE_INSIGHTS:
        return {"status": "disabled", "period": period, "users": 0, "persisted": 0}
    try:
        async with SessionLocal() as db:
            service = ProactiveInsightService(db)
            event_ids = await service.stage_periodic(period)
            await db.commit()
        for event_id in event_ids:
            enqueue_proactive_processing(event_id)
        return {"period": period, "users": len(event_ids), "enqueued": len(event_ids)}
    finally:
        await engine.dispose()


async def _evaluate_due() -> dict:
    from app.proactive_insights.service import ProactiveInsightService

    if not settings.ENABLE_PROACTIVE_INSIGHTS:
        return {"status": "disabled", "enqueued": 0}
    try:
        async with SessionLocal() as db:
            event_ids = await ProactiveInsightService(db).stage_due_periodic()
            await db.commit()
        for event_id in event_ids:
            enqueue_proactive_processing(event_id)
        return {"enqueued": len(event_ids)}
    finally:
        await engine.dispose()


async def _deliver_notification(delivery_id: int) -> dict:
    from app.proactive_insights.notifications import InsightNotificationService

    if not settings.ENABLE_PROACTIVE_INSIGHTS or not settings.PROACTIVE_PUSH_ENABLED:
        return {"status": "disabled"}
    try:
        async with SessionLocal() as db:
            return await InsightNotificationService(db).deliver(delivery_id)
    finally:
        await engine.dispose()


async def _sweep_notifications() -> dict:
    if not settings.ENABLE_PROACTIVE_INSIGHTS or not settings.PROACTIVE_PUSH_ENABLED:
        return {"status": "disabled", "deliveries": 0}
    now = dt.datetime.now(dt.UTC)
    try:
        async with SessionLocal() as db:
            delivery_ids = list(
                (
                    await db.scalars(
                        select(InsightNotificationDelivery.id)
                        .where(
                            InsightNotificationDelivery.status.in_(("scheduled", "failed")),
                            InsightNotificationDelivery.scheduled_for <= now,
                            InsightNotificationDelivery.attempts
                            < settings.PROACTIVE_NOTIFICATION_RETRY_MAX,
                        )
                        .order_by(InsightNotificationDelivery.scheduled_for)
                        .limit(100)
                    )
                ).all()
            )
        for delivery_id in delivery_ids:
            deliver_proactive_notification.delay(delivery_id)
        return {"deliveries": len(delivery_ids)}
    finally:
        await engine.dispose()
