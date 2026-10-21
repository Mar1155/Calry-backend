import string

import pytest

from app.ai.providers.openrouter import _LANGUAGE_NAMES
from app.api.v1.routes import meal_completion, memory
from app.dependencies.auth import _SUPPORTED_LOCALES, _preferred_locale

APP_LOCALES = {"en", "it", "es", "zh", "ja", "ar", "de", "fr"}


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_app_locale_is_supported() -> None:
    assert set(_SUPPORTED_LOCALES) == APP_LOCALES
    assert set(_LANGUAGE_NAMES) == APP_LOCALES
    assert set(memory._SUMMARY_TEASER) == APP_LOCALES


@pytest.mark.parametrize(
    "table",
    [
        meal_completion._CATEGORY_LABELS,
        meal_completion._EVIDENCE_LABELS,
        meal_completion._REVIEW_COPY,
        meal_completion._DAY_REVIEW_COPY,
        meal_completion.GUARDRAIL_MSG,
    ],
)
def test_meal_completion_copy_covers_every_locale(table: dict) -> None:
    assert set(table) == APP_LOCALES
    for locale, copy in table.items():
        assert copy.keys() == table["en"].keys(), locale
        for key, text in copy.items():
            # An unknown placeholder would raise KeyError in str.format.
            assert _placeholders(text) <= _placeholders(table["en"][key]), (locale, key)


@pytest.mark.parametrize(("header", "expected"), [("de-DE,de;q=0.9", "de"), ("fr_FR", "fr"), ("pt-BR", "en")])
def test_new_users_get_german_and_french_locales(header: str, expected: str) -> None:
    assert _preferred_locale(header) == expected
