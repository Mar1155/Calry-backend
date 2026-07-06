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
    task_track_started=True,
    timezone="UTC",
)
