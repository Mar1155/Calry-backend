"""Regression: a worker task must never reuse a pooled client bound to an
earlier task's (closed) event loop. In production a proactive-insight task
left the OpenRouter client open and the next photo analysis in the same
worker process failed with "Event loop is closed"."""

import asyncio

import httpx

from app.ai.providers import openrouter
from app.services import stream_bus
from app.worker import async_runner


async def _client_identity() -> httpx.AsyncClient:
    return openrouter.get_shared_client()


def test_shared_http_client_is_recreated_for_a_new_event_loop() -> None:
    # Two bare asyncio.run() calls with no cleanup: the old failure mode.
    first = asyncio.run(_client_identity())
    second = asyncio.run(_client_identity())

    assert second is not first
    assert not second.is_closed


def test_shared_http_client_is_reused_within_one_loop() -> None:
    async def twice() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
        return openrouter.get_shared_client(), openrouter.get_shared_client()

    a, b = asyncio.run(twice())
    assert a is b


def test_redis_client_is_recreated_for_a_new_event_loop() -> None:
    async def get() -> object:
        return stream_bus.get_redis()

    assert asyncio.run(get()) is not asyncio.run(get())


def test_run_task_releases_loop_bound_clients(monkeypatch) -> None:
    closed: list[str] = []

    class FakeEngine:
        async def dispose(self) -> None:
            closed.append("db_engine")

    monkeypatch.setattr("app.db.session.engine", FakeEngine())

    async def task() -> httpx.AsyncClient:
        stream_bus.get_redis()
        return openrouter.get_shared_client()

    client = async_runner.run_task(task())

    assert client.is_closed
    assert openrouter._shared_client is None
    assert stream_bus._redis is None
    assert closed == ["db_engine"]


def test_run_task_cleans_up_when_the_task_fails(monkeypatch) -> None:
    class FakeEngine:
        async def dispose(self) -> None:
            return None

    monkeypatch.setattr("app.db.session.engine", FakeEngine())
    seen: list[httpx.AsyncClient] = []

    async def boom() -> None:
        seen.append(openrouter.get_shared_client())
        raise RuntimeError("provider down")

    try:
        async_runner.run_task(boom())
    except RuntimeError:
        pass

    assert seen[0].is_closed
    assert openrouter._shared_client is None
