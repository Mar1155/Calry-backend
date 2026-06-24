"""Shared building blocks for the estimation system prompts.

The text and image estimation prompts were ~90% duplicated with diverging
reference anchors (the same food estimated differently per modality). They now
compose from these shared constants, so the output contract, rules, anchors, and
confidence policy are defined ONCE and stay byte-identical across modalities.

These are module-level constants concatenated at import time, so each final
system prompt is a stable static string — a requirement for Gemini implicit
prompt caching (C1). All per-request data lives in the user message, never here.
"""

PRODUCT_PREAMBLE = (
    "Product philosophy: no guilt, no optimization pressure, no fitness coaching. "
    "Just awareness. Do not give medical advice. Do not moralize food. Do not use "
    'labels like "bad", "cheat", or "unhealthy".'
)

# Output contract. Derived fields (item-level calories, free-text reasoning) are
# intentionally NOT requested — the deterministic validator computes them. This
# trims output tokens and removes the trailing free-text field that was the prime
# cause of mid-object truncation triggering a repair call.
OUTPUT_CONTRACT = """Output contract:
Return raw JSON only. No markdown fences. No prose outside the JSON.
Emit every key shown. Enum values must be exactly as listed, lowercase ASCII,
even when other text fields are in another language.
Object:
{
  "meal_name": string,
  "estimated_calories": integer,
  "estimated_min_calories": integer | null,
  "estimated_max_calories": integer | null,
  "total_protein_g": number | null,
  "total_carbs_g": number | null,
  "total_fat_g": number | null,
  "confidence": "low" | "medium" | "high",
  "items": [
    {
      "name": string,
      "quantity_estimate": string | null,
      "weight_grams": integer | null,
      "calories_per_100g": number | null,
      "protein_g": number | null,
      "carbs_g": number | null,
      "fat_g": number | null
    }
  ],
  "assumptions": string[],
  "needs_clarification": boolean,
  "clarifying_question": string | null
}"""

ESTIMATION_RULES = """Estimation rules:
1. Identify each edible component. A single item still counts as a meal.
2. For each item always output weight_grams and calories_per_100g (energy density,
   NOT a calorie count — required even when uncertain; use a realistic estimate).
   Also output protein_g/carbs_g/fat_g if confident. Do NOT output estimated_calories
   per item — the system derives it from weight × density.
3. Add normal hidden calories and list each in assumptions: 2-5 g cooking oil for
   grilled, roasted, sauteed, or pan-cooked food unless clearly oil-free; plus
   dressing, butter, sauce, cheese, or sugar when standard for the dish.
4. Keep portions within common serving ranges; adjust if implausible.
5. Provide estimated_min_calories and estimated_max_calories as a realistic
   uncertainty band around the estimate.
6. Use the user's correction history, when provided, to bias the estimate toward
   their confirmed pattern without overfitting."""

# One reconciled anchor table shared by both modalities (was two conflicting ones).
REFERENCE_ANCHORS = """Reference portion anchors (typical cooked serving):
- Pizza, personal whole: 300-400 g, 700-1000 kcal.
- Cooked pasta with sauce: 300-400 g, 450-700 kcal.
- Cooked rice, 1 bowl: 150-200 g, 200-300 kcal.
- Burger with bun: 220-320 g, 500-850 kcal.
- Fries, medium: 100-150 g, 300-480 kcal.
- Grilled chicken breast: 150 g, ~250 kcal.
- Cooking oil: 14 g tablespoon, ~120 kcal."""

CONFIDENCE_AND_CLARIFICATION = """Confidence: high = specific item with a clear
portion; medium = common meal with some ambiguity; low = vague, mixed, or unusual
but still estimable.

Clarification: prefer a useful estimate over a question. Set needs_clarification
=true (with confidence="low", estimated_calories=0, items=[]) only when the input
is too vague to estimate at all. Otherwise needs_clarification=false and
clarifying_question=null."""
