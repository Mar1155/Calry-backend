import pytest
from app.ai.schemas.meal_estimate import MealEstimateResult, MealEstimateItem
from app.ai.services.validation_service import AIValidationService


def test_validation_macro_derived_calories():
    """Verifies that item calories are adjusted if they deviate significantly from their macro-derived values."""
    # Oats with macros: P=5g (20 kcal), C=30g (120 kcal), F=3g (27 kcal) -> Total = 167 kcal
    # Stated calories is 300 kcal (deviation of 133 kcal, which is > 15 kcal and > 15%)
    item = MealEstimateItem(
        name="Oats",
        quantity_estimate="50g",
        protein_g=5.0,
        carbs_g=30.0,
        fat_g=3.0,
        estimated_calories=300
    )
    result = MealEstimateResult(
        meal_name="Oatmeal",
        estimated_calories=300,
        confidence="high",
        source_type="text",
        items=[item],
        assumptions=[],
        needs_clarification=False,
        model_name="test-model",
        prompt_version="test-v1"
    )
    
    validated = AIValidationService.validate_and_normalize_estimate(result)
    
    # Derivation: 5*4 + 30*4 + 3*9 = 167
    assert validated.items[0].estimated_calories == 167
    assert validated.estimated_calories == 167
    assert validated.total_protein_g == 5.0
    assert validated.total_carbs_g == 30.0
    assert validated.total_fat_g == 3.0


def test_validation_energy_density_clamping():
    """Verifies that items with unrealistic energy density (>9.0 kcal/g) are clamped to pure fat density."""
    # 10g food item claiming 150 calories -> 15.0 kcal/g density
    item = MealEstimateItem(
        name="Mystery Butter",
        quantity_estimate="10g",
        weight_grams=10,
        protein_g=0.0,
        carbs_g=0.0,
        fat_g=15.0,
        estimated_calories=150
    )
    result = MealEstimateResult(
        meal_name="High Density Meal",
        estimated_calories=150,
        confidence="high",
        source_type="text",
        items=[item],
        assumptions=[],
        needs_clarification=False,
        model_name="test-model",
        prompt_version="test-v1"
    )
    
    validated = AIValidationService.validate_and_normalize_estimate(result)
    
    # Should clamp calories to 10g * 9 kcal/g = 90 kcal
    assert validated.items[0].estimated_calories == 90
    assert validated.estimated_calories == 90
    # Recalculated fat should be round(90 / 9) = 10.0g
    assert validated.items[0].fat_g == 10.0
    assert validated.items[0].protein_g == 0.0
    assert validated.items[0].carbs_g == 0.0


def test_validation_missing_macros_fallback():
    """Verifies that if macro data is entirely missing, we do not override the estimated calories."""
    item = MealEstimateItem(
        name="Legacy Salad",
        quantity_estimate="1 plate",
        protein_g=None,
        carbs_g=None,
        fat_g=None,
        estimated_calories=150
    )
    result = MealEstimateResult(
        meal_name="Salad",
        estimated_calories=150,
        confidence="high",
        source_type="text",
        items=[item],
        assumptions=[],
        needs_clarification=False,
        model_name="test-model",
        prompt_version="test-v1"
    )
    
    validated = AIValidationService.validate_and_normalize_estimate(result)
    
    assert validated.items[0].estimated_calories == 150
    assert validated.total_protein_g is None
    assert validated.total_carbs_g is None
    assert validated.total_fat_g is None
