"""Runs a worker task's coroutine in its own event loop, then releases every
resource bound to that loop.

Each Celery task calls ``asyncio.run()``, which opens a fresh loop and closes
it when the task ends. Pooled resources created during the task (the shared
OpenRouter httpx client, the Redis stream-bus client, the SQLAlchemy engine's
connections) are bound to that loop; left open, the next task in the same
worker process can pick them up and fail with "Event loop is closed". That is
how a proactive-insight task that called OpenRouter broke the next photo
analysis in production.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger("app.worker.async_runner")


async def release_loop_resources() -> None:
    from app.ai.providers.openrouter import close_shared_client
    from app.db.session import engine
    from app.services.stream_bus import close_redis

    for name, close in (
        ("openrouter", close_shared_client),
        ("redis", close_redis),
        ("db_engine", engine.dispose),
    ):
        try:
            await close()
        except Exception as exc:  # noqa: BLE001 - cleanup must never mask the task result
            logger.warning("event=worker_loop_cleanup_failed resource=%s error=%s", name, type(exc).__name__)


async def _with_cleanup[T](coro: Coroutine[Any, Any, T]) -> T:
    try:
        return await coro
    finally:
        await release_loop_resources()


def run_task[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(_with_cleanup(coro))
