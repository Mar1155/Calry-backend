import logging

from app.ai.schemas.meal_estimate import MealEstimateResult

logger = logging.getLogger("app.ai.validation")


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

                sum_protein += item.protein_g
                sum_carbs += item.carbs_g
                sum_fat += item.fat_g

                # Calculate macro-derived calories: protein*4 + carbs*4 + fat*9
                macro_derived_calories = int(round(item.protein_g * 4 + item.carbs_g * 4 + item.fat_g * 9))
            else:
                macro_derived_calories = None

            if item.calories_per_100g is None and item.weight_grams and item.weight_grams > 0:
                if item.estimated_calories > 0:
                    item.calories_per_100g = round(item.estimated_calories / item.weight_grams * 100, 1)
                elif macro_derived_calories is not None:
                    item.calories_per_100g = round(macro_derived_calories / item.weight_grams * 100, 1)

            # Energy density check: max 900 kcal / 100g (pure fat).
            if item.calories_per_100g is not None and item.calories_per_100g > 900.0:
                logger.warning(
                    f"Unrealistic energy density ({item.calories_per_100g:.1f} kcal/100g) for item '{item.name}'. Clamping to 900 kcal/100g."
                )
                item.calories_per_100g = 900.0
                if has_macros and item.weight_grams is not None:
                    item.protein_g = 0.0
                    item.carbs_g = 0.0
                    item.fat_g = round(item.weight_grams, 1)

            if item.weight_grams and item.weight_grams > 0 and item.calories_per_100g is not None:
                item.estimated_calories = int(round(item.weight_grams * item.calories_per_100g / 100))
            elif macro_derived_calories is not None:
                item.estimated_calories = macro_derived_calories
            else:
                item.estimated_calories = max(0, item.estimated_calories)

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

        # 6. Check total calories constraints and align with items
        if result.items:
            # Align total calories to sum of items if difference is significant
            if abs(result.estimated_calories - sum_item_calories) > 10:
                logger.info(
                    f"Calorie discrepancy: total={result.estimated_calories}, sum of items={sum_item_calories}. Aligning to sum of items."
                )
                result.estimated_calories = sum_item_calories
                result.assumptions.append("Adjusted total calories to match sum of items.")
        else:
            if result.estimated_calories < 0:
                result.estimated_calories = 0

        # 7. Clamp final total calories to 5000
        if result.estimated_calories > 5000:
            logger.warning(f"Unusually high calorie estimate: {result.estimated_calories}. Clamping to 5000.")
            result.estimated_calories = 5000
            result.assumptions.append("Clamped estimate to 5000 kcal max.")
            result.confidence = "low"

        # 8. Clamp min/max ranges if they exist
        if result.estimated_min_calories is not None:
            result.estimated_min_calories = max(0, result.estimated_min_calories)
        if result.estimated_max_calories is not None:
            result.estimated_max_calories = max(result.estimated_calories, result.estimated_max_calories)

        return result
