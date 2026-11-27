"""Quantities the person wrote are facts, not suggestions.

The model sometimes reinterprets an explicit amount: "250 g di pasta al ragù"
became a 250 g cooked plate (160 g of pasta), and the estimate was off by a
factor of three. This module finds the amounts a person stated in their own
words ("250g di pasta", "2 barrette da 35g", "pasta 180 gr", "200 ml di latte")
and, after the model answers, puts each one back on the ingredient it names.

Deliberately conservative. An amount is enforced only when it is tied to a
food phrase without ambiguity and that phrase matches exactly one estimated
ingredient. When the amount could describe a whole dish ("pasta e fagioli
300g") or the ingredient was converted to another state ("cooked pasta" from a
dry weight), nothing is changed and the case is only reported.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult

# Weight of the stated amount and the model's weight may differ by this much
# before the stated amount is put back (rounding, "about").
TOLERANCE = 0.05

_UNIT_GRAMS: dict[str, float] = {
    "g": 1.0,
    "gr": 1.0,
    "grammi": 1.0,
    "grammo": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "gramos": 1.0,
    "gramm": 1.0,
    "grammes": 1.0,
    "kg": 1000.0,
    "chili": 1000.0,
    "ml": 1.0,
    "cl": 10.0,
    "l": 1000.0,
    "lt": 1000.0,
    "litro": 1000.0,
    "litri": 1000.0,
    "liter": 1000.0,
    "liters": 1000.0,
    "litre": 1000.0,
    "litres": 1000.0,
    "oz": 28.35,
}
_VOLUME_UNITS = {"ml", "cl", "l", "lt", "litro", "litri", "liter", "liters", "litre", "litres"}

_NUMBER = r"(\d+(?:[.,]\d+)?)"
_UNIT = "|".join(sorted(_UNIT_GRAMS, key=len, reverse=True))
_QUANTITY_RE = re.compile(rf"{_NUMBER}\s*({_UNIT})\.?(?![a-zà-ÿ])", re.IGNORECASE)

_COUNT_WORDS = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "a": 1,
    "an": 1,
    "one": 1,
    "un'": 1,
    "due": 2,
    "two": 2,
    "dos": 2,
    "deux": 2,
    "zwei": 2,
    "tre": 3,
    "three": 3,
    "tres": 3,
    "trois": 3,
    "drei": 3,
    "quattro": 4,
    "four": 4,
    "cuatro": 4,
    "quatre": 4,
    "vier": 4,
    "cinque": 5,
    "five": 5,
    "cinco": 5,
    "cinq": 5,
    "fünf": 5,
    "sei": 6,
    "six": 6,
    "seis": 6,
    "sechs": 6,
}
# A connector ends a food phrase: what follows is another food or a sauce.
_CONNECTORS = {
    "e",
    "ed",
    "con",
    "al",
    "alla",
    "allo",
    "alle",
    "ai",
    "agli",
    "all",
    "in",
    "più",
    "piu",
    "and",
    "with",
    "plus",
    "on",
    "y",
    "et",
    "avec",
    "und",
    "mit",
    "+",
    "&",
}
# Marks a per-piece amount: "2 barrette da 35g", "35g l'una", "35 g each".
_PER_PIECE_BEFORE = {"da", "of", "de", "à", "a", "von", "je"}
_PER_PIECE_AFTER_RE = re.compile(
    r"^\s*(l['’ ]?un[ao]|ciascun[ao]?|cadaun[ao]|cad\.?|each|apiece|per piece|"
    r"a testa|al pezzo|cada una?|chacune?|pièce|pro stück|je)\b",
    re.IGNORECASE,
)
# Words that say nothing about which food: dropped before matching.
_COOKING_WORDS = {
    "grilled",
    "roasted",
    "baked",
    "fried",
    "steamed",
    "boiled",
    "griglia",
    "grigliato",
    "grigliata",
    "grigliati",
    "grigliate",
    "fritto",
    "fritta",
    "fritti",
    "fritte",
    "arrosto",
    "lesso",
    "vapore",
    "forno",
    "plancha",
    "asado",
    "asada",
    "gebraten",
    "gegrillt",
    "grillé",
    "grillée",
    "rôti",
}
_NON_FOOD = {
    "di",
    "d",
    "del",
    "della",
    "dello",
    "dei",
    "degli",
    "delle",
    "of",
    "de",
    "la",
    "le",
    "il",
    "lo",
    "the",
    "some",
    "circa",
    "about",
    "around",
    "ca",
    "pesata",
    "pesato",
    "pesati",
    "pesate",
    "weighed",
    "cruda",
    "crudo",
    "crudi",
    "crude",
    "raw",
    "dry",
    "secca",
    "secco",
    "secchi",
    "cotta",
    "cotto",
    "cotti",
    "cotte",
    "cooked",
    "da",
    "una",
    "uno",
    "un",
    "a",
    "an",
    "manciata",
    "handful",
    "porzione",
    "portion",
    "piatto",
    "plate",
    "bowl",
    "ciotola",
}
# Dry-state energy (kcal/100 g) for foods people weigh dry. Used only when the
# model turned a stated dry amount into a cooked one: the stated grams then
# need the dry density, not the cooked one the model chose.
_DRY_DENSITY: dict[str, float] = {
    "pasta": 357,
    "spagh": 357,
    "penne": 357,
    "fusil": 357,
    "rigat": 357,
    "macch": 357,
    "lingu": 357,
    "farfa": 357,
    "bucat": 357,
    "orecc": 357,
    "tagli": 366,
    "noodl": 357,
    "riso": 350,
    "rice": 350,
    "basma": 350,
    "risot": 350,
    "cousc": 370,
    "bulgu": 342,
    "avena": 372,
    "oats": 380,
    "fiocc": 372,
    "farro": 335,
    "orzo": 352,
    "quino": 368,
    "lenti": 325,
    "ceci": 335,
    "chick": 364,
    "fagio": 300,
    "beans": 333,
}
_COOKED_WORDS = ("cott", "cooked", "bollit", "lessat", "boiled", "cocid", "cuit", "gekocht")
# Commas split foods, but never a decimal comma ("1,5 kg").
_SPLIT_RE = re.compile(r"[;\n]+|(?<!\d),|,(?!\d)")
# Apostrophes split words: "d'avena" is "d" + "avena".
_TOKEN_RE = re.compile(r"[a-zà-ÿ0-9]+|[+&]")


@dataclass
class StatedQuantity:
    grams: float
    food_tokens: list[str]
    phrase: str
    is_volume: bool = False


@dataclass
class QuantityLockReport:
    stated: list[StatedQuantity] = field(default_factory=list)
    enforced: list[str] = field(default_factory=list)
    honored: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {
            "stated": len(self.stated),
            "honored": self.honored,
            "enforced": self.enforced,
            "unresolved": self.unresolved,
        }


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_fold(text).replace("’", "'"))


def _food_tokens(words: list[str]) -> list[str]:
    out = []
    for word in words:
        word = word.strip("'")
        if (
            len(word) >= 3
            and word not in _NON_FOOD
            and word not in _COOKING_WORDS
            and word not in _COUNT_WORDS
            and not word.isdigit()
        ):
            out.append(word)
    return out


def _phrase_after(words: list[str]) -> list[str]:
    taken: list[str] = []
    for word in words:
        if word in _CONNECTORS or _QUANTITY_RE.fullmatch(word):
            break
        taken.append(word)
        if len(taken) == 5:
            break
    return taken


def _phrase_before(words: list[str]) -> list[str] | None:
    """Words before a trailing amount ("pasta 250g"). None when the run holds
    a connector: "pasta e fagioli 300g" weighs the dish, not the beans."""
    if any(word in _CONNECTORS for word in words):
        return None
    return words[-6:]


def _count(words: list[str]) -> int | None:
    for word in words[:2]:
        word = word.strip("'")
        if word.isdigit() and 0 < int(word) <= 20:
            return int(word)
        if word in _COUNT_WORDS:
            return _COUNT_WORDS[word]
    return None


def extract_stated_quantities(text: str | None) -> list[StatedQuantity]:
    if not text:
        return []
    found: list[StatedQuantity] = []
    for clause in _SPLIT_RE.split(_fold(text)):
        for match in _QUANTITY_RE.finditer(clause):
            number = float(match.group(1).replace(",", "."))
            unit = match.group(2).lower()
            grams = number * _UNIT_GRAMS[unit]
            before = _tokens(clause[: match.start()])
            # Only the run since the previous amount belongs to this one.
            previous = list(_QUANTITY_RE.finditer(clause[: match.start()]))
            if previous:
                before = _tokens(clause[previous[-1].end() : match.start()])
            rest = clause[match.end() :]
            after = _tokens(rest)
            per_piece_after = bool(_PER_PIECE_AFTER_RE.match(rest))

            phrase: list[str] | None
            per_piece = per_piece_after
            # The food follows the amount ("250g di pasta", "200 g Quark")
            # unless only the words before it can name it ("pasta 250g").
            before_is_free = not _food_tokens(before) or before[-1] in _CONNECTORS
            if (after and after[0] in {"di", "d", "of", "de", "von"}) or (
                after and not per_piece_after and _food_tokens(_phrase_after(after)) and before_is_free
            ):
                phrase = _phrase_after(after[1:] if after[0] in {"di", "d", "of", "de", "von"} else after)
                count_words = before
            else:
                if before and before[-1] in _PER_PIECE_BEFORE and _count(before):
                    per_piece = True
                    before = before[:-1]
                phrase = _phrase_before(before)
                count_words = before
            if not phrase:
                continue
            food = _food_tokens(phrase)
            if not food:
                continue
            count = _count(count_words) if per_piece else None
            if per_piece and count:
                grams *= count
            found.append(
                StatedQuantity(
                    grams=round(grams, 1),
                    food_tokens=food,
                    phrase=" ".join(phrase),
                    is_volume=unit in _VOLUME_UNITS,
                )
            )
    return found


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def _item_stems(item: MealEstimateItem) -> set[str]:
    return {_stem(w) for w in _food_tokens(_tokens(item.name))}


def _matches(stated: StatedQuantity, item: MealEstimateItem) -> bool:
    """Every food word the person wrote must name this ingredient. "torta di
    mele 150g" never matches the apples inside a decomposed cake: 150 g is
    the slice, not the fruit."""
    stems = _item_stems(item)
    return bool(stated.food_tokens) and all(_stem(w) in stems for w in stated.food_tokens)


def _partly_matches(stated: StatedQuantity, item: MealEstimateItem) -> bool:
    stems = _item_stems(item)
    return any(_stem(w) in stems for w in stated.food_tokens)


def _says_cooked(text: str) -> bool:
    folded = _fold(text)
    return any(word in folded for word in _COOKED_WORDS)


def _dry_density(stated: StatedQuantity, item: MealEstimateItem) -> float | None:
    for word in [*stated.food_tokens, *_food_tokens(_tokens(item.name))]:
        density = _DRY_DENSITY.get(_stem(word))
        if density is not None:
            return density
    return None


def _restore_dry(item: MealEstimateItem, grams: int, density: float) -> None:
    old_kcal = item.estimated_calories or ((item.weight_grams or 0) * (item.calories_per_100g or 0) / 100)
    item.weight_grams = grams
    item.calories_per_100g = density
    item.estimated_calories = int(round(grams * density / 100))
    factor = item.estimated_calories / old_kcal if old_kcal else None
    for attr in ("protein_g", "carbs_g", "fat_g"):
        value = getattr(item, attr)
        if value is not None and factor is not None:
            setattr(item, attr, round(value * factor, 1))
    for word in ("cotta", "cotto", "cotti", "cotte", "cooked", "Cotta", "Cotto", "Cooked"):
        item.name = item.name.replace(f" {word}", "")
    item.quantity_estimate = f"{grams} g"


def _rescale_item(item: MealEstimateItem, grams: int) -> None:
    old = item.weight_grams or 0
    factor = grams / old if old > 0 else None
    item.weight_grams = grams
    if factor is not None:
        for attr in ("protein_g", "carbs_g", "fat_g"):
            value = getattr(item, attr)
            if value is not None:
                setattr(item, attr, round(value * factor, 1))
    if item.calories_per_100g is not None:
        item.estimated_calories = int(round(grams * item.calories_per_100g / 100))


def _recompute_totals(result: MealEstimateResult) -> None:
    old_total = result.estimated_calories
    result.estimated_calories = sum(item.estimated_calories for item in result.items)
    if any(getattr(i, a) is not None for i in result.items for a in ("protein_g", "carbs_g", "fat_g")):
        result.total_protein_g = round(sum(i.protein_g or 0 for i in result.items), 1)
        result.total_carbs_g = round(sum(i.carbs_g or 0 for i in result.items), 1)
        result.total_fat_g = round(sum(i.fat_g or 0 for i in result.items), 1)
    if old_total > 0:
        ratio = result.estimated_calories / old_total
        if result.estimated_min_calories is not None:
            result.estimated_min_calories = round(result.estimated_min_calories * ratio)
        if result.estimated_max_calories is not None:
            result.estimated_max_calories = round(result.estimated_max_calories * ratio)
    if result.estimated_min_calories is not None:
        result.estimated_min_calories = min(result.estimated_min_calories, result.estimated_calories)
    if result.estimated_max_calories is not None:
        result.estimated_max_calories = max(result.estimated_max_calories, result.estimated_calories)


def apply_stated_quantities(text: str | None, result: MealEstimateResult) -> QuantityLockReport:
    """Put every unambiguous stated amount back on its ingredient. Mutates
    ``result`` and returns what was checked, kept, changed, or left alone."""
    report = QuantityLockReport(stated=extract_stated_quantities(text))
    if not report.stated or result.needs_clarification or not result.items:
        return report

    changed = False
    for stated in report.stated:
        label = f"{stated.phrase}={stated.grams:g}g"
        matches = [item for item in result.items if _matches(stated, item)]
        if not matches:
            partial = any(_partly_matches(stated, item) for item in result.items)
            report.unresolved.append(f"{label}:{'partial_match' if partial else 'no_item'}")
            continue
        weight = sum(item.weight_grams or 0 for item in matches)
        tolerance = TOLERANCE * (2 if stated.is_volume else 1)
        if weight and abs(weight - stated.grams) <= tolerance * stated.grams:
            report.honored.append(label)
            continue
        if len(matches) > 1:
            report.unresolved.append(f"{label}:several_items")
            continue
        item = matches[0]
        if _says_cooked(f"{item.name} {item.quantity_estimate or ''}") and not _says_cooked(stated.phrase):
            # The model turned a dry amount into cooked weight. Put the stated
            # dry grams back with a dry density when the food is a known dry
            # staple; otherwise leave it and report it.
            dry = _dry_density(stated, item)
            if dry is None:
                report.unresolved.append(f"{label}:state_converted")
                continue
            _restore_dry(item, int(round(stated.grams)), dry)
            report.enforced.append(f"{label}:was_{weight}g_cooked")
            changed = True
            continue
        _rescale_item(item, int(round(stated.grams)))
        report.enforced.append(f"{label}:was_{weight}g")
        changed = True

    if changed:
        _recompute_totals(result)
        result.stated_quantity_enforced = True
    return report
