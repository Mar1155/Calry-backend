"""Unit tests for the fail-safe incremental meal-estimate parser.

These lock in the two guarantees the design depends on:
  1. Only fully-closed, schema-validated units are ever emitted.
  2. Any ambiguity latches the parser off — it never emits a guess, and the
     caller's full-JSON fallback takes over.
"""

import json

from app.ai.streaming.partial_parser import StreamingMealParser

FULL = {
    "meal_name": "Grilled chicken salad",
    "estimated_calories": 420,
    "confidence": "high",
    "items": [
        {"name": "Grilled chicken breast", "weight_grams": 150, "calories_per_100g": 165,
         "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
        {"name": "Mixed greens", "weight_grams": 80, "calories_per_100g": 20},
        {"name": "Olive oil", "weight_grams": 10, "calories_per_100g": 884},
    ],
    "assumptions": ["Standard portion"],
    "needs_clarification": False,
}


def _feed_char_by_char(text: str) -> list[tuple[str, object]]:
    p = StreamingMealParser()
    events: list[tuple[str, object]] = []
    for c in text:
        events.extend(p.feed(c))
    return events


def _feed_in_chunks(text: str, size: int) -> list[tuple[str, object]]:
    p = StreamingMealParser()
    events: list[tuple[str, object]] = []
    for i in range(0, len(text), size):
        events.extend(p.feed(text[i : i + size]))
    return events


def test_emits_name_then_all_items_char_by_char():
    text = json.dumps(FULL)
    events = _feed_char_by_char(text)

    names = [v for t, v in events if t == "meal_name"]
    items = [v for t, v in events if t == "item"]

    assert names == ["Grilled chicken salad"]
    assert len(items) == 3
    assert [it["name"] for it in items] == [
        "Grilled chicken breast",
        "Mixed greens",
        "Olive oil",
    ]


def test_derives_estimated_calories_from_density():
    events = _feed_char_by_char(json.dumps(FULL))
    first = next(v for t, v in events if t == "item")
    # 150g * 165/100 = 247.5 -> 248
    assert first["estimated_calories"] == 248
    # explicit numeric fields pass through
    assert first["protein_g"] == 31.0


def test_robust_across_arbitrary_chunk_boundaries():
    text = json.dumps(FULL)
    for size in (1, 2, 3, 5, 7, 13, 64, 4096):
        items = [v for t, v in _feed_in_chunks(text, size) if t == "item"]
        assert len(items) == 3, f"chunk size {size} lost items"


def test_braces_inside_string_values_do_not_miscount():
    payload = {
        "meal_name": "Weird {name} [with] braces",
        "items": [
            {"name": "Item with } and { inside", "weight_grams": 100, "calories_per_100g": 50},
        ],
    }
    events = _feed_char_by_char(json.dumps(payload))
    names = [v for t, v in events if t == "meal_name"]
    items = [v for t, v in events if t == "item"]
    assert names == ["Weird {name} [with] braces"]
    assert len(items) == 1
    assert items[0]["name"] == "Item with } and { inside"


def test_no_emission_until_object_fully_closed():
    p = StreamingMealParser()
    partial = '{"meal_name": "Soup", "items": [ {"name": "Broth", "weight_grams": 200'
    events = p.feed(partial)
    # name is closed -> emitted; the single item object is NOT closed -> withheld
    assert ("meal_name", "Soup") in events
    assert all(t != "item" for t, _ in events)
    assert not p.disabled
    # Close it and the item now appears.
    events2 = p.feed(', "calories_per_100g": 30} ]}')
    assert [t for t, _ in events2] == ["item"]


def test_latches_off_on_invalid_item_and_stops_emitting():
    # Second item has a non-numeric weight -> invalid -> latch off.
    bad = (
        '{"meal_name":"Meal","items":['
        '{"name":"Good","weight_grams":100,"calories_per_100g":50},'
        '{"name":"Bad","weight_grams":"heavy","calories_per_100g":50},'
        '{"name":"NeverSeen","weight_grams":10,"calories_per_100g":50}]}'
    )
    p = StreamingMealParser()
    events = p.feed(bad)
    items = [v for t, v in events if t == "item"]
    assert [it["name"] for it in items] == ["Good"]  # only the valid one
    assert p.disabled
    # Further feeds emit nothing.
    assert p.feed("garbage") == []


def test_latches_off_on_unparseable_but_balanced_span():
    # Balanced braces but invalid JSON inside the array.
    bad = '{"meal_name":"X","items":[ {name without quotes} ]}'
    p = StreamingMealParser()
    events = p.feed(bad)
    assert all(t != "item" for t, _ in events)
    assert p.disabled


def test_empty_items_array_closes_cleanly_without_disabling():
    events = _feed_char_by_char('{"meal_name":"Water","items":[]}')
    assert ("meal_name", "Water") in events
    assert all(t != "item" for t, _ in events)


def test_missing_name_field_never_fabricates_one():
    events = _feed_char_by_char('{"items":[{"name":"A","weight_grams":10,"calories_per_100g":5}]}')
    assert all(t != "meal_name" for t, _ in events)
    items = [v for t, v in events if t == "item"]
    assert len(items) == 1
