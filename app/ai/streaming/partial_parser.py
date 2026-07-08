"""Fail-safe incremental extractor for a streaming meal-estimate JSON object.

The model streams one JSON object (see ``MEAL_ESTIMATE_RESPONSE_SCHEMA``) token
by token. This parser watches the growing buffer and emits *preview* units the
instant they are unambiguously complete:

  * the top-level ``meal_name`` string, once its closing quote arrives;
  * each object inside the top-level ``items`` array, once its matching brace
    closes AND it validates against a minimal shape.

Design contract — **fail safe, never guess**:

  * Only fully-closed, schema-validated units are emitted. A half-streamed
    object is never emitted.
  * On ANY ambiguity — a completed object that fails ``json.loads``, an item
    that fails minimal validation, or an unexpected token where a value was
    expected — the parser *latches off* (``disabled``). Once disabled it emits
    nothing further for the rest of the stream.
  * The caller ALWAYS runs an authoritative full-JSON parse of the accumulated
    text when the stream ends, regardless of what (if anything) was previewed.
    Previews are a UX accelerant, never the source of truth.

The parser has no knowledge of transport, persistence, or the wider result
model; it yields plain ``("meal_name", str)`` / ``("item", dict)`` tuples.
"""

from __future__ import annotations

import json
import re

# `"meal_name" : "…"` with a *closed* string value (handles escaped quotes).
# Requiring the closing quote is what makes a partial value impossible to match.
_MEAL_NAME_RE = re.compile(r'"meal_name"\s*:\s*"((?:[^"\\]|\\.)*)"')
# `"items" : [` — start of the array we stream element-by-element.
_ITEMS_OPEN_RE = re.compile(r'"items"\s*:\s*\[')

_NUMERIC_KEYS = (
    "weight_grams",
    "calories_per_100g",
    "protein_g",
    "carbs_g",
    "fat_g",
    "estimated_calories",
)


class StreamingMealParser:
    """Feed streamed text chunks; get back fully-formed preview units.

    Not thread-safe; drive it from a single stream consumer.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._disabled = False

        self._name_emitted = False

        self._items_open = False
        self._items_closed = False
        self._scan = 0  # resume index for object scanning (points at buffer)
        self._emitted_items = 0

    @property
    def disabled(self) -> bool:
        """True once the parser has latched off partial emission."""
        return self._disabled

    @property
    def emitted_items(self) -> int:
        return self._emitted_items

    def feed(self, chunk: str) -> list[tuple[str, object]]:
        """Append a streamed chunk; return any newly-completed preview units.

        Returns a list of ``("meal_name", str)`` and ``("item", dict)`` tuples,
        in stream order. Returns ``[]`` once the parser is disabled.
        """
        if self._disabled or not chunk:
            if not chunk:
                return []
            self._buf += chunk
            return []

        self._buf += chunk
        events: list[tuple[str, object]] = []

        if not self._name_emitted:
            m = _MEAL_NAME_RE.search(self._buf)
            if m:
                try:
                    name = json.loads('"' + m.group(1) + '"')
                except (json.JSONDecodeError, ValueError):
                    # A malformed name means the buffer is not trustworthy.
                    self._disabled = True
                    return events
                self._name_emitted = True
                if isinstance(name, str) and name.strip():
                    events.append(("meal_name", name))
                # An empty/whitespace name is not worth previewing, but it is not
                # an error either — skip it without latching off.

        events.extend(self._scan_items())
        return events

    # -- items --------------------------------------------------------------

    def _scan_items(self) -> list[tuple[str, object]]:
        if self._disabled or self._items_closed:
            return []

        if not self._items_open:
            km = _ITEMS_OPEN_RE.search(self._buf)
            if not km:
                return []
            self._items_open = True
            self._scan = km.end()

        out: list[tuple[str, object]] = []
        buf = self._buf
        n = len(buf)
        i = self._scan

        while i < n:
            ch = buf[i]
            if ch in " \t\r\n,":
                i += 1
                self._scan = i
                continue
            if ch == "]":
                # Array closed cleanly — no more items will ever arrive.
                self._items_closed = True
                self._scan = i + 1
                break
            if ch == "{":
                end = self._match_object(i)
                if end is None:
                    # Object still streaming; wait for more input.
                    break
                obj_str = buf[i : end + 1]
                try:
                    obj = json.loads(obj_str)
                except (json.JSONDecodeError, ValueError):
                    # A closed brace-balanced span that still won't parse means
                    # our scan desynced from the real structure. Bail out.
                    self._disabled = True
                    return out
                if not self._valid_item(obj):
                    self._disabled = True
                    return out
                out.append(("item", self._normalize_item(obj)))
                self._emitted_items += 1
                i = end + 1
                self._scan = i
                continue
            # Anything other than an object/`]`/separator at the array's top
            # level is not something we understand — stop guessing.
            self._disabled = True
            return out

        return out

    def _match_object(self, start: int) -> int | None:
        """Index of the ``}`` closing the object opened at ``start``, or None.

        String-aware so braces inside string values never miscount depth.
        Returns None while the object is still incomplete.
        """
        buf = self._buf
        n = len(buf)
        depth = 0
        in_str = False
        esc = False
        i = start
        while i < n:
            c = buf[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return i
            i += 1
        return None

    # -- validation / normalization ----------------------------------------

    @staticmethod
    def _valid_item(obj: object) -> bool:
        """Minimal shape gate. A preview item must have a usable name and any
        present numeric fields must actually be numbers (or null)."""
        if not isinstance(obj, dict):
            return False
        name = obj.get("name")
        if not isinstance(name, str) or not name.strip():
            return False
        for key in _NUMERIC_KEYS:
            if key in obj and obj[key] is not None and not isinstance(obj[key], (int, float)):
                return False
        # Booleans are ints in Python — reject them as numeric values.
        for key in _NUMERIC_KEYS:
            if isinstance(obj.get(key), bool):
                return False
        return True

    @staticmethod
    def _normalize_item(obj: dict) -> dict:
        """Shape a validated raw item into the client preview contract, deriving
        estimated_calories from density × weight when the model omitted it."""

        def num(key: str) -> float | int | None:
            v = obj.get(key)
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        weight = num("weight_grams")
        cal100 = num("calories_per_100g")
        est = num("estimated_calories")
        if est is None and weight is not None and cal100 is not None:
            est = round(cal100 * weight / 100)

        return {
            "name": obj.get("name", ""),
            "quantity_estimate": obj.get("quantity_estimate")
            if isinstance(obj.get("quantity_estimate"), str)
            else None,
            "weight_grams": int(weight) if weight is not None else None,
            "calories_per_100g": cal100,
            "protein_g": num("protein_g"),
            "carbs_g": num("carbs_g"),
            "fat_g": num("fat_g"),
            "estimated_calories": int(est) if est is not None else None,
        }
