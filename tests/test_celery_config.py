from app.core.config import settings
from app.worker.celery_app import celery_app


def test_worker_defaults_bound_memory() -> None:
    assert celery_app.conf.worker_concurrency == settings.CELERY_WORKER_CONCURRENCY == 1
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.worker_max_tasks_per_child == 20
    assert celery_app.conf.worker_max_memory_per_child == 384_000
    assert celery_app.conf.task_ignore_result is True
