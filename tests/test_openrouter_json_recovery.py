from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.meal_estimate import MealEstimateResult
from app.ai.services.validation_service import AIValidationService


def test_recovers_partial_meal_estimate_from_truncated_json() -> None:
    raw_output = """{
  "meal_name": "Gourmet Beef Burger",
  "estimated_calories": 850,
  "meal_name": "Gourmet Beef Burger",
  "estimated_min_calories": 750,
  "estimated_max_calories": 950,
  "total_protein_g": 55.0,
  "estimated_calories": 850,
  "estimated_min_calories": 750,
  "total_carbs_g": 50.0,
  "total_fat_g": 50.0,
  "confidence": "high",
  "items": [
    {
      "name": "Brioche Burger Bun",
      "quantity_estimate": "1 bun",
      "weight_grams": 100,
      "protein_g":"""

    parsed = OpenRouterProvider._recover_partial_json(raw_output, MealEstimateResult)

    assert parsed is not None
    assert parsed["meal_name"] == "Gourmet Beef Burger"
    assert parsed["estimated_calories"] == 850
    assert parsed["estimated_min_calories"] == 750
    assert parsed["estimated_max_calories"] == 950
    assert parsed["confidence"] == "high"
    assert parsed["items"][0]["estimated_calories"] == 850

    result = MealEstimateResult(
        **parsed,
        source_type="photo",
        model_name="test-model",
        prompt_version="test-prompt",
        raw_output=raw_output,
    )
    validated = AIValidationService.validate_and_normalize_estimate(result)

    assert validated.estimated_calories == 850
    assert validated.total_protein_g == 55.0
    assert validated.total_carbs_g == 50.0
    assert validated.total_fat_g == 50.0
