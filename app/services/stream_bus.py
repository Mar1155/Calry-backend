"""Cross-process relay for photo meal-analysis streaming.

The photo path runs analysis in a Celery worker (the durable executor) while a
`/photo/stream` request in the API process relays partial results to the client
live. The two processes never share memory, so they rendezvous through Redis:

  * The worker ``publish``es each protocol event to a per-job channel AND folds
    it into a per-job *state snapshot* (meal_name + accumulated items + terminal
    event), so a client that connects late — or reconnects after a dropped
    stream — can replay everything it missed before tailing live.
  * The relay subscribes to the channel, replays the snapshot (deduped by item
    index), then tails live events until a terminal (`done`/`error`) arrives.

Snapshots carry a TTL; the relay falls back to the durable DB job row when the
snapshot has expired (see the `/photo/stream` route).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger("app.services.stream_bus")

STATE_TTL_SECONDS = 3600

# redis is a runtime dependency (installed via celery[redis]) but imported
# lazily so the meals router — and every non-photo endpoint — still imports on
# an API-only environment where the worker deps aren't installed.
_redis: Any = None


def get_redis() -> Any:
    global _redis
    if _redis is None:
        from redis import asyncio as aioredis

        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def close_redis() -> None:
    """Close the per-loop Redis client. The Celery worker runs each task in a
    fresh event loop, so its connection must be closed at task end."""
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis close failed: {e}")
        _redis = None


def _chan(job_id: str) -> str:
    return f"meal_stream:chan:{job_id}"


def _state_key(job_id: str) -> str:
    return f"meal_stream:state:{job_id}"


async def publish(job_id: str, event: dict) -> None:
    """Fold ``event`` into the job snapshot, then publish it to the channel.

    Snapshot-before-publish ordering means a relay that reads the snapshot
    immediately after receiving a published event never sees a snapshot that
    lags the channel."""
    r = get_redis()
    try:
        await _update_state(r, job_id, event)
        await r.publish(_chan(job_id), json.dumps(event, ensure_ascii=False, default=str))
    except Exception as e:  # noqa: BLE001 — streaming is best-effort; never break the worker
        logger.warning(f"stream_bus.publish failed for job {job_id}: {e}")


async def _update_state(r: aioredis.Redis, job_id: str, event: dict) -> None:
    key = _state_key(job_id)
    raw = await r.get(key)
    state = json.loads(raw) if raw else {"meal_name": None, "items": [], "terminal": None}
    t = event.get("type")
    if t == "meal_name":
        state["meal_name"] = event
    elif t == "item":
        state["items"].append(event)
    elif t in ("done", "error"):
        state["terminal"] = event
    await r.set(key, json.dumps(state, ensure_ascii=False, default=str), ex=STATE_TTL_SECONDS)


async def read_state(job_id: str) -> dict | None:
    r = get_redis()
    raw = await r.get(_state_key(job_id))
    return json.loads(raw) if raw else None


async def subscribe(job_id: str):
    """Return a subscribed pubsub handle for the job channel."""
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_chan(job_id))
    return pubsub
