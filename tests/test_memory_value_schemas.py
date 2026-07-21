import pytest
from pydantic import ValidationError

from app.memory.value_schemas import (
    CalibrationValue,
    PortionValue,
    PreferenceValue,
    dump_value,
    validate_value,
)


def test_portion_value_validates() -> None:
    value = PortionValue(canonical_key="pasta", display_name="Pasta", grams=120, grams_low=100, grams_high=140, sample_count=6)
    assert value.schema_version == 1
    assert value.grams == 120


def test_preference_value_types() -> None:
    value = PreferenceValue(canonical_key="oats", display_name="Oats", preference_type="regular", occurrences=6, distinct_days=4)
    assert value.preference_type == "regular"


def test_calibration_value_bounds() -> None:
    value = CalibrationValue(
        scope="overall",
        sample_count=10,
        confirmed_without_edit_count=8,
        no_edit_rate=0.8,
        within_5pct_count=7,
        within_5pct_rate=0.7,
        median_abs_correction_percent=3.2,
    )
    assert value.no_edit_rate == 0.8


def test_validate_value_dispatches_by_domain() -> None:
    raw = {"canonical_key": "rice", "display_name": "Rice", "grams": 90, "grams_low": 80, "grams_high": 100, "sample_count": 5}
    assert isinstance(validate_value("portion_model", raw), PortionValue)


def test_validate_value_unknown_domain_raises() -> None:
    with pytest.raises(ValueError):
        validate_value("seasonality", {})


def test_extra_fields_are_forbidden() -> None:
    raw = {
        "canonical_key": "pasta",
        "display_name": "Pasta",
        "grams": 120,
        "grams_low": 100,
        "grams_high": 140,
        "sample_count": 6,
        "unexpected": True,
    }
    with pytest.raises(ValidationError):
        validate_value("portion_model", raw)


def test_wrong_schema_version_rejected() -> None:
    raw = {
        "schema_version": 2,
        "canonical_key": "pasta",
        "display_name": "Pasta",
        "grams": 120,
        "grams_low": 100,
        "grams_high": 140,
        "sample_count": 6,
    }
    with pytest.raises(ValidationError):
        validate_value("portion_model", raw)


def test_dump_roundtrip() -> None:
    value = PortionValue(canonical_key="pasta", display_name="Pasta", grams=120, grams_low=100, grams_high=140, sample_count=6)
    assert validate_value("portion_model", dump_value(value)) == value
