"""Deterministic narration for memories (Phase 1).

The narrator is a pure template renderer. It verbalizes ONLY fields present in a
moment's ``fact_json`` (or a belief's validated value) and never invents numbers
or claims. There is no LLM on this path. Non-English locales fall back to English
in Phase 1; localized templates arrive in Phase 2.
"""

from typing import Any

PROMPT_VERSION = "memory_template_v1"
MODEL_VERSION = "template"

_SUPPORTED_LOCALES = {"en", "it", "es", "zh", "ja", "ar"}

_SCOPE_LABELS = {
    "overall": "your meals",
    "source:photo": "your photo estimates",
    "source:text": "your text estimates",
    "source:voice": "your voice estimates",
}


def _resolve_locale(locale: str | None) -> str:
    primary = (locale or "en").split(",")[0].split("-")[0].split("_")[0].strip().lower()
    return primary if primary in _SUPPORTED_LOCALES else "en"


def _grams(value: Any) -> str:
    return f"{int(value)} g"


# --- moment narration -------------------------------------------------------


def _discovery_portion(fact: dict) -> str:
    return f"I've learned that your {fact.get('display_name', 'meal')} is usually around {_grams(fact.get('grams', 0))}."


def _discovery_preference(fact: dict) -> str:
    name = fact.get("display_name", "meal")
    if fact.get("preference_type") == "favourite":
        return f"I recognize {name} as one of your favourites."
    return f"{name.capitalize()} has become a regular for you."


def _learning(fact: dict) -> str:
    label = _SCOPE_LABELS.get(fact.get("scope", "overall"), "your meals")
    return f"I've stopped needing corrections for most of {label}."


def _calibration(fact: dict) -> str:
    label = _SCOPE_LABELS.get(fact.get("scope", "overall"), "your meals")
    return f"I now estimate {label} within about 5%."


def _evolution_portion(fact: dict) -> str:
    return (
        f"Your {fact.get('display_name', 'meal')} portions have shifted from "
        f"{_grams(fact.get('old_grams', 0))} to around {_grams(fact.get('grams', 0))}."
    )


def _evolution_generic(fact: dict) -> str:
    return f"What I believe about your {fact.get('display_name', 'eating')} has changed."


_MILESTONE_TEXT = {
    "meals_confirmed": "It's been {count} meals since I started learning how you eat.",
    "days_together": "It's been {count} days since your first meal with me.",
    "foods_learned": "I've now learned {count} of your foods.",
}


def _milestone(fact: dict) -> str:
    template = _MILESTONE_TEXT.get(fact.get("milestone", ""), "A quiet milestone in how I've learned you.")
    return template.format(count=fact.get("count", 0))


def render_moment(moment_kind: str, domain: str, fact: dict, locale: str | None = None) -> str:
    """Render one grounded sentence for a moment. Falls back to a neutral line if a
    specific template is missing, never to fabricated detail."""
    _resolve_locale(locale)  # Phase 1: en only; accepted for forward compatibility.
    if moment_kind == "discovery" and domain == "portion_model":
        return _discovery_portion(fact)
    if moment_kind == "discovery" and domain == "preference":
        return _discovery_preference(fact)
    if moment_kind == "learning":
        return _learning(fact)
    if moment_kind == "calibration":
        return _calibration(fact)
    if moment_kind == "evolution" and domain == "portion_model":
        return _evolution_portion(fact)
    if moment_kind == "evolution":
        return _evolution_generic(fact)
    if moment_kind == "milestone":
        return _milestone(fact)
    return "I've noticed something about how you eat."


# --- belief current-statement narration -------------------------------------


def render_belief_statement(domain: str, value: dict, locale: str | None = None) -> str:
    """A calm present-tense statement of a living belief (the 'What Calry knows' view)."""
    _resolve_locale(locale)
    if domain == "portion_model":
        return f"Your {value.get('display_name', 'meal')} is usually around {_grams(value.get('grams', 0))}."
    if domain == "preference":
        name = value.get("display_name", "meal")
        if value.get("preference_type") == "favourite":
            return f"{name.capitalize()} is one of your favourites."
        return f"You regularly eat {name}."
    if domain == "ai_calibration":
        label = _SCOPE_LABELS.get(value.get("scope", "overall"), "your meals")
        return f"I estimate {label} within about 5%."
    return "I've learned something about how you eat."
