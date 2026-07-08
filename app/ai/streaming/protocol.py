"""NDJSON streaming protocol shared by the meal-analysis stream endpoints and
the photo worker. One JSON object per line; each carries a ``type`` field.

Event types (client contract):
  status    {"type":"status","stage":"uploading|transcribing|processing"}
  meal_name {"type":"meal_name","meal_name": str}
  item      {"type":"item","index": int,"item": {...preview item...}}   # preview only
  done      {"type":"done","meal": {...full persisted MealResponse...}}  # authoritative
  error     {"type":"error","code": str,"message": str}

`item` events are best-effort previews (raw, pre-validation, may be dropped
mid-stream if the extractor latches off). `done` is always sent on success and
carries the validated, persisted meal — the client must treat it as the source
of truth and reconcile any previewed items against it.
"""

from __future__ import annotations

import json

NDJSON_MEDIA_TYPE = "application/x-ndjson"


def line(event: dict) -> str:
    """Serialize one event as a single NDJSON line (newline-terminated)."""
    return json.dumps(event, ensure_ascii=False, default=str) + "\n"


def status(stage: str) -> dict:
    return {"type": "status", "stage": stage}


def meal_name(name: str) -> dict:
    return {"type": "meal_name", "meal_name": name}


def item(index: int, preview: dict) -> dict:
    return {"type": "item", "index": index, "item": preview}


def done(meal: dict) -> dict:
    return {"type": "done", "meal": meal}


def error(code: str, message: str) -> dict:
    return {"type": "error", "code": code, "message": message}
