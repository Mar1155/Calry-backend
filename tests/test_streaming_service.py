"""Integration test for the streaming estimation service.

Stubs only the provider's network stream; everything downstream — the
incremental parser, the authoritative full-JSON parse, validation, and
finalization — is the real code path. Locks the event contract the routes and
photo worker depend on: previews arrive first, then a single `__complete__`
carrying the validated MealEstimateResult built from the full accumulated text.
"""

import pytest

from app.ai.services.calorie_estimation_service import AICalorieEstimationService

_FULL_JSON = (
    '{"meal_name":"Oatmeal bowl","estimated_calories":350,"confidence":"high",'
    '"total_protein_g":12,"total_carbs_g":55,"total_fat_g":7,'
    '"items":['
    '{"name":"Oats","weight_grams":80,"calories_per_100g":380,"protein_g":13,"carbs_g":66,"fat_g":7},'
    '{"name":"Banana","weight_grams":100,"calories_per_100g":89,"protein_g":1.1,"carbs_g":23,"fat_g":0.3}'
    '],"assumptions":[],"needs_clarification":false}'
)


def _chunks(text: str, size: int = 17) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


async def _fake_provider_stream(chunks):
    for c in chunks:
        yield {"delta": c}
    yield {"meta": {"usage": None, "latency_ms": 10, "raw_text": "".join(chunks)}}


@pytest.mark.asyncio
async def test_stream_estimate_from_text_previews_then_complete(db_session, monkeypatch):
    svc = AICalorieEstimationService(db_session)
    provider = svc.providers["openrouter"]

    async def fake_stream(*args, **kwargs):
        async for ev in _fake_provider_stream(_chunks(_FULL_JSON)):
            yield ev

    monkeypatch.setattr(provider, "stream_meal_from_text", fake_stream)

    events = []
    async for ev in svc.stream_estimate_from_text("oatmeal and banana", user_id=None):
        events.append(ev)

    # Terminal event is the authoritative, validated result.
    assert events[-1]["type"] == "__complete__"
    result = events[-1]["result"]
    assert result.meal_name == "Oatmeal bowl"
    assert len(result.items) == 2
    assert result.estimated_calories > 0
    assert result.confidence in {"low", "medium", "high"}
    assert result.confidence_score is not None  # finalize ran

    # Previews arrived before the terminal event.
    names = [e["meal_name"] for e in events if e.get("type") == "meal_name"]
    assert names == ["Oatmeal bowl"]
    item_previews = [e for e in events if e.get("type") == "item"]
    assert [e["item"]["name"] for e in item_previews] == ["Oats", "Banana"]
    # Preview items are indexed in order.
    assert [e["index"] for e in item_previews] == [0, 1]
    # Derived preview calories: 80g * 380/100 = 304.
    assert item_previews[0]["item"]["estimated_calories"] == 304


@pytest.mark.asyncio
async def test_stream_result_built_from_full_text_even_with_no_previews(db_session, monkeypatch):
    """If the incremental parser never fires (e.g. it latched off early), the
    service must still produce a validated result from the full text."""
    svc = AICalorieEstimationService(db_session)
    provider = svc.providers["openrouter"]

    # Deliver the whole JSON as one final chunk so the parser has no partial
    # boundaries to emit on, then the authoritative parse takes over.
    async def fake_stream(*args, **kwargs):
        yield {"meta": {"usage": None, "latency_ms": 5, "raw_text": _FULL_JSON}}

    monkeypatch.setattr(provider, "stream_meal_from_text", fake_stream)

    events = []
    async for ev in svc.stream_estimate_from_text("oatmeal and banana", user_id=None):
        events.append(ev)

    assert events[-1]["type"] == "__complete__"
    result = events[-1]["result"]
    assert result.meal_name == "Oatmeal bowl"
    assert len(result.items) == 2
