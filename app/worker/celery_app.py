import logging
from urllib.parse import urlsplit

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready, worker_shutdown

from app.core.config import settings
from app.worker.health import WorkerHealthServer

logger = logging.getLogger("app.worker.celery")
health_server: WorkerHealthServer | None = None

celery_app = Celery(
    "calry",
    broker=settings.REDIS_URL,
    backend=settings.CELERY_RESULT_BACKEND or settings.REDIS_URL,
    include=["app.tasks.meal_analysis", "app.tasks.memory", "app.tasks.proactive_insights"],
)

celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    result_serializer="json",
    task_serializer="json",
    # Job state/results are persisted in meal_analysis_jobs. Avoid storing an
    # unused second copy in Redis.
    task_ignore_result=True,
    task_track_started=True,
    timezone="UTC",
    # Photo analysis is memory-heavy and latency is dominated by the remote AI
    # call. One prefork child avoids multiplying imports by Railway's visible
    # CPU count while preserving terminate=True task cancellation.
    worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=settings.CELERY_WORKER_MAX_TASKS_PER_CHILD,
    worker_max_memory_per_child=settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB,
    # Nightly memory consolidation applies pure decay/status transitions for users
    # with no recent meal events. Requires a `celery beat` process to fire.
    beat_schedule={
        "memory-nightly-consolidation": {
            "task": "app.tasks.memory.consolidate_memory",
            "schedule": crontab(hour=3, minute=0),
        },
        "proactive-insights-pending-sweep": {
            "task": "app.tasks.proactive_insights.sweep_pending",
            "schedule": crontab(minute="*/5"),
        },
        "proactive-insights-notification-sweep": {
            "task": "app.tasks.proactive_insights.sweep_notifications",
            "schedule": crontab(minute="*"),
        },
        "proactive-insights-timezone-aware-evaluation": {
            "task": "app.tasks.proactive_insights.evaluate_due",
            "schedule": crontab(minute="*/15"),
        },
    },
)


@worker_ready.connect
def log_worker_ready(**_: object) -> None:
    global health_server
    if health_server is None:
        health_server = WorkerHealthServer("0.0.0.0", settings.PORT)
        health_server.start()

    broker = urlsplit(settings.REDIS_URL)
    logger.info(
        "event=meal_analysis_worker_ready broker_scheme=%s broker_host=%s concurrency=%s max_retries=%s",
        broker.scheme,
        broker.hostname,
        settings.CELERY_WORKER_CONCURRENCY,
        settings.MEAL_ANALYSIS_MAX_RETRIES,
    )


@worker_shutdown.connect
def log_worker_shutdown(**_: object) -> None:
    global health_server
    if health_server is not None:
        health_server.stop()
        health_server = None
    logger.warning("event=meal_analysis_worker_shutdown")
