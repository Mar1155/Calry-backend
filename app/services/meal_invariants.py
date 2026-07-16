from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ai.schemas.meal_estimate import MealEstimateResult

_GRAMS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*g\b", re.IGNORECASE)
_MAX_DENSITY = 900.0


class InvalidMealIngredients(ValueError):
    pass


def _item_dict(item: dict[str, Any] | Any) -> dict[str, Any]:
    return item.copy() if isinstance(item, dict) else item.model_dump()


def _fallback_weight(quantity: str | None) -> int:
    match = _GRAMS_RE.search(quantity or "")
    if match:
        try:
            parsed = float(match.group(1).replace(",", "."))
            if math.isfinite(parsed):
                return max(1, int(math.floor(parsed + 0.5)))
        except (OverflowError, ValueError):
            pass
    return 100


def _ingredient_calories(weight_grams: int, calories_per_100g: float) -> int:
    calories = weight_grams * calories_per_100g / 100
    if not math.isfinite(calories) or calories < 0:
        raise InvalidMealIngredients("Ingredient calories must be finite and non-negative.")
    return int(math.floor(calories + 0.5))


def _assign_calories(item: dict[str, Any], calories: int) -> None:
    minimum_weight = int(math.ceil(calories * 100 / _MAX_DENSITY)) if calories else 1
    if minimum_weight > 2_147_483_647:
        raise InvalidMealIngredients("Ingredient quantity exceeds the supported range.")
    previous_weight = item["weight_grams"]
    item["weight_grams"] = max(previous_weight, minimum_weight)
    item["calories_per_100g"] = calories * 100 / item["weight_grams"]
    if item["weight_grams"] != previous_weight:
        item["quantity_estimate"] = f'{item["weight_grams"]} g'


def normalize_meal_ingredients(
    items: Iterable[dict[str, Any] | Any],
    *,
    meal_name: str | None,
    target_calories: int | None,
) -> tuple[list[dict[str, Any]], int]:
    """Return valid ingredients and their exact derived calorie sum.

    Legacy/provider calorie totals are used only to repair missing ingredient
    density. Once every ingredient is calculable, the ingredient sum is the
    sole source of truth.
    """
    normalized = [_item_dict(item) for item in items]
    try:
        target = max(0, int(target_calories or 0))
    except (OverflowError, TypeError, ValueError):
        target = 0
    if not normalized:
        weight = max(100, int(math.ceil(target * 100 / _MAX_DENSITY))) if target else 100
        normalized = [
            {
                "name": (meal_name or "Meal ingredient").strip() or "Meal ingredient",
                "quantity_estimate": f"{weight} g",
                "weight_grams": weight,
                "calories_per_100g": target * 100 / weight,
            }
        ]

    unresolved: list[dict[str, Any]] = []
    resolved_total = 0
    for item in normalized:
        item["name"] = str(item.get("name") or meal_name or "Meal ingredient").strip()
        if not item["name"]:
            item["name"] = "Meal ingredient"

        weight = item.get("weight_grams")
        try:
            parsed_weight = float(weight) if weight is not None else 0
            weight = int(math.floor(parsed_weight + 0.5)) if math.isfinite(parsed_weight) else 0
        except (OverflowError, TypeError, ValueError):
            weight = 0
        if weight <= 0:
            weight = _fallback_weight(item.get("quantity_estimate"))
        item["weight_grams"] = weight
        item["quantity_estimate"] = item.get("quantity_estimate") or f"{weight} g"

        density = item.get("calories_per_100g")
        try:
            density = float(density) if density is not None else None
        except (TypeError, ValueError):
            density = None
        if density is not None and math.isfinite(density) and density >= 0:
            if density > _MAX_DENSITY:
                legacy_calories = _ingredient_calories(weight, density)
                weight = max(weight, int(math.ceil(legacy_calories * 100 / _MAX_DENSITY)))
                item["weight_grams"] = weight
                density = legacy_calories * 100 / weight
                item["quantity_estimate"] = f"{weight} g"
            item["calories_per_100g"] = density
            resolved_total += _ingredient_calories(weight, density)
            continue

        explicit_calories = item.get("estimated_calories")
        try:
            parsed_calories = float(explicit_calories)
            explicit_calories = max(0, int(math.floor(parsed_calories + 0.5))) if math.isfinite(parsed_calories) else 0
        except (OverflowError, TypeError, ValueError):
            explicit_calories = 0
        if explicit_calories > 0:
            _assign_calories(item, explicit_calories)
            resolved_total += explicit_calories
        else:
            unresolved.append(item)

    remaining = max(0, target - resolved_total)
    for index, item in enumerate(unresolved):
        slots = len(unresolved) - index
        allocated = remaining if slots == 1 else int(round(remaining / slots))
        remaining -= allocated
        _assign_calories(item, allocated)

    for item in normalized:
        if (
            item["weight_grams"] <= 0
            or item["weight_grams"] > 2_147_483_647
            or item["calories_per_100g"] < 0
            or item["calories_per_100g"] > _MAX_DENSITY
        ):
            raise InvalidMealIngredients("Every ingredient needs a positive quantity and calorie density.")
        item["estimated_calories"] = _ingredient_calories(
            item["weight_grams"],
            item["calories_per_100g"],
        )

    derived_total = sum(item["estimated_calories"] for item in normalized)
    return normalized, derived_total


def enforce_estimate_ingredient_invariants(result: MealEstimateResult) -> MealEstimateResult:
    from app.ai.schemas.meal_estimate import MealEstimateItem

    items, total = normalize_meal_ingredients(
        result.items,
        meal_name=result.meal_name,
        target_calories=result.estimated_calories,
    )
    result.items = [MealEstimateItem(**item) for item in items]
    result.estimated_calories = total
    if result.estimated_min_calories is not None:
        result.estimated_min_calories = min(result.estimated_min_calories, total)
    if result.estimated_max_calories is not None:
        result.estimated_max_calories = max(result.estimated_max_calories, total)
    return result
