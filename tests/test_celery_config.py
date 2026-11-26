from app.core.config import settings
from app.worker.celery_app import celery_app


def test_worker_defaults_bound_memory() -> None:
    assert celery_app.conf.worker_concurrency == settings.CELERY_WORKER_CONCURRENCY == 1
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.worker_max_tasks_per_child == 20
    assert celery_app.conf.worker_max_memory_per_child == 384_000
    assert celery_app.conf.task_ignore_result is True


def test_proactive_insight_periodic_schedule_is_registered() -> None:
    schedule = celery_app.conf.beat_schedule
    assert {
        "proactive-insights-pending-sweep",
        "proactive-insights-timezone-aware-evaluation",
        "proactive-insights-notification-sweep",
    }.issubset(schedule)


def test_meal_nudge_planner_is_scheduled() -> None:
    entry = celery_app.conf.beat_schedule["meal-nudges-plan"]
    assert entry["task"] == "app.tasks.notifications.plan_meal_nudges"
    assert "app.tasks.notifications" in celery_app.conf.include
