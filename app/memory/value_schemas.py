"""Versioned, domain-specific schemas for ``MemoryBelief.value_json``.

Every belief payload is validated against a frozen, ``extra="forbid"`` schema
that carries its own ``schema_version``. Unknown domains or unexpected fields
raise rather than silently persisting shapeless JSON.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryValueSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1


class PortionValue(MemoryValueSchema):
    """A learned typical portion for one canonical food (domain=portion_model)."""

    schema_version: Literal[1] = 1
    canonical_key: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=500)
    grams: int = Field(ge=1)
    grams_low: int = Field(ge=1)
    grams_high: int = Field(ge=1)
    sample_count: int = Field(ge=1)


class PreferenceValue(MemoryValueSchema):
    """An explicit or strongly-evidenced food preference (domain=preference)."""

    schema_version: Literal[1] = 1
    canonical_key: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=500)
    # favourite = explicit user flag; regular = high confirmed frequency over days.
    preference_type: Literal["favourite", "regular"]
    occurrences: int = Field(ge=1)
    distinct_days: int = Field(ge=1)


class CalibrationValue(MemoryValueSchema):
    """The AI's measured estimation accuracy on this user (domain=ai_calibration)."""

    schema_version: Literal[1] = 1
    # "overall" | "source:photo" | "source:text" | "source:voice"
    scope: str = Field(min_length=1, max_length=40)
    sample_count: int = Field(ge=1)
    confirmed_without_edit_count: int = Field(ge=0)
    no_edit_rate: float = Field(ge=0, le=1)
    within_5pct_count: int = Field(ge=0)
    within_5pct_rate: float = Field(ge=0, le=1)
    median_abs_correction_percent: float = Field(ge=0)


VALUE_SCHEMAS: dict[str, type[MemoryValueSchema]] = {
    "portion_model": PortionValue,
    "preference": PreferenceValue,
    "ai_calibration": CalibrationValue,
}


def validate_value(domain: str, raw: dict) -> MemoryValueSchema:
    """Validate a raw value_json against its domain schema. Raises ValueError on
    unknown domain and pydantic.ValidationError on a malformed payload."""
    schema = VALUE_SCHEMAS.get(domain)
    if schema is None:
        raise ValueError(f"Unknown memory domain: {domain}")
    return schema.model_validate(raw)


def dump_value(value: MemoryValueSchema) -> dict:
    return value.model_dump(mode="json")
