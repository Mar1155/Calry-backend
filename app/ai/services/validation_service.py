import logging

from app.ai.density_table import lookup_food
from app.ai.schemas.meal_estimate import MealEstimateResult

logger = logging.getLogger("app.ai.validation")

# Energy density of pure fat — the hard physical ceiling for any food (kcal/100g).
MAX_ENERGY_DENSITY = 900.0
MAX_TOTAL_CALORIES = 5000
# Macro-derived vs density-derived calories disagreeing by more than this fraction
# is treated as a quality signal (lowers confidence), not silently averaged away.
MACRO_MISMATCH_TOLERANCE = 0.15


class AIValidationService:
    """Service to validate and normalize meal estimation responses from AI providers."""

    @staticmethod
    def validate_and_normalize_estimate(result: MealEstimateResult) -> MealEstimateResult:
        # 1. Normalize empty meal name
        if not result.meal_name or not result.meal_name.strip():
            result.meal_name = "Meal"

        # 2. Normalize assumptions
        if result.assumptions is None:
            result.assumptions = []

        # 3. Handle clarification edge case
        if result.needs_clarification:
            if not result.clarifying_question or not result.clarifying_question.strip():
                result.clarifying_question = "Could you tell me more about what you ate?"
            result.estimated_calories = 0
            result.estimated_min_calories = None
            result.estimated_max_calories = None
            result.items = []
            result.confidence = "low"
            result.total_protein_g = 0.0
            result.total_carbs_g = 0.0
            result.total_fat_g = 0.0
            return result

        # 4. Check / normalize items and derive item calories from per-100g density.
        sum_item_calories = 0
        sum_protein = 0.0
        sum_carbs = 0.0
        sum_fat = 0.0
        any_item_has_macros = False

        for item in result.items:
            # Normalize item name
            item.name = item.name.strip().title() if item.name else "Unknown Item"

            # Normalize weight
            if item.weight_grams is not None:
                item.weight_grams = max(0, item.weight_grams)
            if item.calories_per_100g is not None:
                item.calories_per_100g = round(max(0.0, item.calories_per_100g), 1)

            # Check if macros are provided
            has_macros = (item.protein_g is not None or item.carbs_g is not None or item.fat_g is not None)

            if has_macros:
                any_item_has_macros = True
                # Normalize and clamp macros to non-negative
                item.protein_g = round(max(0.0, item.protein_g), 1) if item.protein_g is not None else 0.0
                item.carbs_g = round(max(0.0, item.carbs_g), 1) if item.carbs_g is not None else 0.0
                item.fat_g = round(max(0.0, item.fat_g), 1) if item.fat_g is not None else 0.0

                # Calculate macro-derived calories: protein*4 + carbs*4 + fat*9
                macro_derived_calories = int(round(item.protein_g * 4 + item.carbs_g * 4 + item.fat_g * 9))
            else:
                macro_derived_calories = None

            # Derive density from explicit calories or macros when the model omitted it.
            if item.calories_per_100g is None and item.weight_grams and item.weight_grams > 0:
                if item.estimated_calories > 0:
                    item.calories_per_100g = round(item.estimated_calories / item.weight_grams * 100, 1)
                elif macro_derived_calories is not None:
                    item.calories_per_100g = round(macro_derived_calories / item.weight_grams * 100, 1)

            # C18: deterministic portion anchoring. Only fires when the model named a
            # food but produced NO usable number (no weight, no density, no macros,
            # no calories) — never overrides an estimate the model actually gave.
            if (
                (item.weight_grams is None or item.weight_grams == 0)
                and item.calories_per_100g is None
                and macro_derived_calories is None
                and item.estimated_calories <= 0
            ):
                ref = lookup_food(item.name)
                if ref is not None:
                    item.weight_grams = ref.typical_portion_g
                    item.calories_per_100g = round((ref.kcal_per_100g_min + ref.kcal_per_100g_max) / 2, 1)
                    result.assumptions.append(
                        f"Estimated {item.name} portion from a typical serving size."
                    )

            # C9: energy-density clamp. Scale macros PROPORTIONALLY to the clamped
            # calorie target instead of fabricating a pure-fat composition.
            if item.calories_per_100g is not None and item.calories_per_100g > MAX_ENERGY_DENSITY:
                logger.warning(
                    f"Unrealistic energy density ({item.calories_per_100g:.1f} kcal/100g) for item "
                    f"'{item.name}'. Clamping to {MAX_ENERGY_DENSITY:.0f} kcal/100g."
                )
                result.density_clamped = True
                if has_macros and item.weight_grams and item.weight_grams > 0:
                    clamped_kcal = item.weight_grams * MAX_ENERGY_DENSITY / 100
                    original_macro_kcal = item.protein_g * 4 + item.carbs_g * 4 + item.fat_g * 9
                    if original_macro_kcal > 0:
                        factor = clamped_kcal / original_macro_kcal
                        item.protein_g = round(item.protein_g * factor, 1)
                        item.carbs_g = round(item.carbs_g * factor, 1)
                        item.fat_g = round(item.fat_g * factor, 1)
                        macro_derived_calories = int(round(
                            item.protein_g * 4 + item.carbs_g * 4 + item.fat_g * 9
                        ))
                item.calories_per_100g = MAX_ENERGY_DENSITY

            # Resolve the item's effective calories.
            density_kcal = None
            if item.weight_grams and item.weight_grams > 0 and item.calories_per_100g is not None:
                density_kcal = int(round(item.weight_grams * item.calories_per_100g / 100))
                item.estimated_calories = density_kcal
            elif macro_derived_calories is not None:
                item.estimated_calories = macro_derived_calories
            else:
                item.estimated_calories = max(0, item.estimated_calories)

            # C17: cross-check the two independent calorie signals. Disagreement is a
            # confidence signal — we trust the density (constrained) value but flag it.
            if density_kcal is not None and macro_derived_calories is not None and density_kcal > 0:
                rel_diff = abs(density_kcal - macro_derived_calories) / density_kcal
                if rel_diff > MACRO_MISMATCH_TOLERANCE:
                    result.macro_mismatch = True

            # Accumulate macro totals from the FINAL (post-clamp) item macros.
            if has_macros:
                sum_protein += item.protein_g
                sum_carbs += item.carbs_g
                sum_fat += item.fat_g

            sum_item_calories += item.estimated_calories

        # 5. Assign macro totals if macros were active
        if any_item_has_macros:
            result.total_protein_g = round(sum_protein, 1)
            result.total_carbs_g = round(sum_carbs, 1)
            result.total_fat_g = round(sum_fat, 1)
        else:
            result.total_protein_g = None
            result.total_carbs_g = None
            result.total_fat_g = None

        # 6. Align total calories to the sum of items when they meaningfully disagree.
        #    Relative tolerance (max of 50 kcal / 10%) avoids realigning on rounding noise.
        if result.items:
            tolerance = max(50, int(round(0.10 * max(result.estimated_calories, sum_item_calories))))
            if abs(result.estimated_calories - sum_item_calories) > tolerance:
                logger.info(
                    f"Calorie discrepancy: total={result.estimated_calories}, "
                    f"sum of items={sum_item_calories}. Aligning to sum of items."
                )
                result.estimated_calories = sum_item_calories
                result.total_realigned = True
        else:
            if result.estimated_calories < 0:
                result.estimated_calories = 0

        # 7. Clamp final total calories
        if result.estimated_calories > MAX_TOTAL_CALORIES:
            logger.warning(f"Unusually high calorie estimate: {result.estimated_calories}. Clamping to {MAX_TOTAL_CALORIES}.")
            result.estimated_calories = MAX_TOTAL_CALORIES
            result.assumptions.append(f"Clamped estimate to {MAX_TOTAL_CALORIES} kcal max.")
            result.density_clamped = True
            # An estimate this extreme is inherently untrustworthy; floor the label
            # (the deterministic confidence score downstream also reflects the clamp).
            result.confidence = "low"

        # 8. Enforce range invariants: estimated_min <= estimated_calories <= estimated_max.
        if result.estimated_min_calories is not None:
            result.estimated_min_calories = max(0, min(result.estimated_min_calories, result.estimated_calories))
        if result.estimated_max_calories is not None:
            result.estimated_max_calories = max(result.estimated_calories, result.estimated_max_calories)

        return result
