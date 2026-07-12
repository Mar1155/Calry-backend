from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "calry",
    broker=settings.REDIS_URL,
    backend=settings.CELERY_RESULT_BACKEND or settings.REDIS_URL,
    include=["app.tasks.meal_analysis"],
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
)
