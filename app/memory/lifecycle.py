"""Pure lifecycle decisions for beliefs: status, value-change, evolution moments.

These helpers are side-effect free; the service mutates rows based on their
output. Keeping the rules here makes them unit-testable in isolation.
"""

import datetime as dt

from app.core.config import settings
from app.memory.confidence import DOMAIN_PARAMS
from app.memory.types import MomentSpec

STATUS_PROVISIONAL = "provisional"
STATUS_ACTIVE = "active"
STATUS_EVOLVING = "evolving"
STATUS_DISPUTED = "disputed"
STATUS_ARCHIVED = "archived"


def status_for(confidence: float, *, domain: str, span_days: int) -> str:
    """Map a reproducible confidence to a non-disputed status.

    Below the archive floor a belief archives (decay). At/above ACTIVE_AT with a
    wide enough span it is active; otherwise provisional.
    """
    if confidence < settings.MEMORY_ARCHIVE_FLOOR:
        return STATUS_ARCHIVED
    params = DOMAIN_PARAMS[domain]
    if confidence >= settings.MEMORY_ACTIVE_AT and span_days >= params.min_span_days:
        return STATUS_ACTIVE
    return STATUS_PROVISIONAL


def portion_diverged(old_value: dict, new_value: dict) -> bool:
    """True when a learned portion shifted beyond the configured tolerance."""
    old_grams = int(old_value.get("grams", 0) or 0)
    new_grams = int(new_value.get("grams", 0) or 0)
    if old_grams <= 0 or new_grams <= 0:
        return False
    return abs(new_grams - old_grams) / old_grams > settings.MEMORY_PORTION_DIVERGENCE_PCT


def preference_changed(old_value: dict, new_value: dict) -> bool:
    return old_value.get("preference_type") != new_value.get("preference_type")


def evolution_moment_for_portion(
    old_value: dict, new_value: dict, *, confidence_at: float, span_days: int, occurred_on: dt.date
) -> MomentSpec:
    old_grams = int(old_value.get("grams", 0) or 0)
    new_grams = int(new_value.get("grams", 0) or 0)
    return MomentSpec(
        moment_kind="evolution",
        domain="portion_model",
        beat_key=f"evo_{old_grams}_{new_grams}",
        fact={
            "display_name": (new_value.get("display_name") or old_value.get("display_name") or "meal")[:120],
            "old_grams": old_grams,
            "grams": new_grams,
        },
        confidence_at=confidence_at,
        evidence_span_days=span_days,
        occurred_on=occurred_on,
    )
