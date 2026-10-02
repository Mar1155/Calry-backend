"""Text/voice analyses outlive the connection: a dropped stream never cancels
the analysis, and reconnecting with the same client_request_id re-attaches to
the run in flight instead of starting (and paying for) a second one."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.ai.streaming import protocol
from app.api.v1.routes import meals
from app.services import stream_bus


class _FakeBus:
    """In-memory stand-in for the Redis-backed stream bus."""

    def __init__(self):
        self.owners: set[str] = set()
        self.state: dict[str, dict] = {}
        self.queues: dict[str, list[asyncio.Queue]] = {}

    async def subscribe(self, job_id):
        queue: asyncio.Queue = asyncio.Queue()
        self.queues.setdefault(job_id, []).append(queue)
        bus = self

        class _PubSub:
            async def get_message(self, ignore_subscribe_messages=True, timeout=5.0):
                try:
                    return await asyncio.wait_for(queue.get(), timeout=min(timeout, 0.05))
                except TimeoutError:
                    return None

            async def unsubscribe(self):
                bus.queues[job_id].remove(queue)

            async def aclose(self):
                pass

        return _PubSub()

    async def publish(self, job_id, event):
        st = self.state.setdefault(job_id, {"meal_name": None, "items": [], "terminal": None})
        if event["type"] == "meal_name":
            st["meal_name"] = event
        elif event["type"] == "item":
            st["items"].append(event)
        elif event["type"] in ("done", "error"):
            st["terminal"] = event
        for q in self.queues.get(job_id, []):
            q.put_nowait({"data": json.dumps(event)})

    async def read_state(self, job_id):
        return self.state.get(job_id)

    async def claim(self, job_id, ttl_seconds):
        if job_id in self.owners:
            return False
        self.owners.add(job_id)
        return True

    async def release(self, job_id):
        self.owners.discard(job_id)

    async def reset_state(self, job_id):
        self.state.pop(job_id, None)


class _NoDb:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def bus(monkeypatch):
    fake = _FakeBus()
    for name in ("subscribe", "publish", "read_state", "claim", "release", "reset_state"):
        monkeypatch.setattr(stream_bus, name, getattr(fake, name))
    monkeypatch.setattr(meals, "SessionLocal", _NoDb)

    async def no_saved_meal(db, user_id, client_request_id):
        return None

    monkeypatch.setattr(meals, "_find_existing_by_request_id", no_saved_meal)
    return fake


def _work(gate: asyncio.Event, calls: list, *, fail: bool = False):
    def work(db):
        async def events():
            calls.append(1)
            yield protocol.status("processing")
            yield protocol.meal_name("Pasta")
            yield protocol.item(0, {"name": "Pasta"})
            await gate.wait()
            if fail:
                yield protocol.error("stream_failed", "AI inference failed.")
            else:
                yield protocol.done({"id": 7, "meal_name": "Pasta"})

        return events()

    return work


async def _drain(stream) -> list[dict]:
    return [json.loads(line) async for line in stream]


_USER = SimpleNamespace(id=1)


async def _settle():
    for _ in range(200):
        if not meals._inline_runs:
            return
        await asyncio.sleep(0.01)  # yield so done-callbacks can run
    raise AssertionError("inline run never finished")


@pytest.mark.asyncio
async def test_a_dropped_connection_does_not_cancel_the_analysis(bus):
    gate, calls = asyncio.Event(), []
    stream = meals._durable_inline_stream(_USER, "req-1", _work(gate, calls))
    first = json.loads(await stream.__anext__())
    assert first["type"] in {"status", "meal_name", "item"}
    await stream.aclose()  # the client went away mid-analysis

    gate.set()
    await _settle()
    assert bus.state["inline:1:req-1"]["terminal"]["type"] == "done"
    assert calls == [1]


@pytest.mark.asyncio
async def test_reconnecting_reattaches_to_the_run_in_flight(bus):
    gate, calls = asyncio.Event(), []
    work = _work(gate, calls)
    first = meals._durable_inline_stream(_USER, "req-2", work)
    await first.__anext__()
    await first.aclose()
    await asyncio.sleep(0)  # let the run publish its partial events

    second = asyncio.create_task(_drain(meals._durable_inline_stream(_USER, "req-2", work)))
    await asyncio.sleep(0.01)
    gate.set()
    events = await second
    await _settle()

    assert calls == [1]  # one analysis, never a second paid run
    types = [e["type"] for e in events]
    assert types[0] == "meal_name" and "item" in types and types[-1] == "done"
    assert [e["index"] for e in events if e["type"] == "item"] == [0]


@pytest.mark.asyncio
async def test_a_failed_run_can_be_retried_with_the_same_request_id(bus):
    gate, calls = asyncio.Event(), []
    gate.set()
    failed = await _drain(meals._durable_inline_stream(_USER, "req-3", _work(gate, calls, fail=True)))
    await _settle()
    assert failed[-1]["type"] == "error"

    retried = await _drain(meals._durable_inline_stream(_USER, "req-3", _work(gate, calls)))
    await _settle()
    assert retried[-1]["type"] == "done"
    assert calls == [1, 1]


@pytest.mark.asyncio
async def test_without_a_request_id_it_runs_inside_the_request(bus):
    gate, calls = asyncio.Event(), []
    gate.set()
    events = await _drain(meals._durable_inline_stream(_USER, None, _work(gate, calls)))
    assert events[-1]["type"] == "done"
    assert not bus.state and not meals._inline_runs
