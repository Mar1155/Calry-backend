from app.ai.prompts._shared import (
    CONFIDENCE_AND_CLARIFICATION,
    ESTIMATION_RULES,
    OUTPUT_CONTRACT,
    PRODUCT_PREAMBLE,
    REFERENCE_ANCHORS,
)

IMAGE_MEAL_ESTIMATION_PROMPT_VERSION = "image_meal_estimation_v5"

_VISUAL_RULES = """Visual estimation:
- If no food is visible: needs_clarification=true, confidence="low",
  estimated_calories=0, items=[], clarifying_question="Try another photo or
  describe the meal with text."
- Estimate total plate weight first using visible scale (dinner plate ~26 cm,
  side plate ~18 cm, cup/glass ~250 ml, and stacking/depth for burgers, fries,
  pasta, rice bowls, lasagna, cakes), then split weight across components.
- Density cues: leafy salad is low density; meat, cheese, grains, and pasta are
  high density.
- Use the user's hint as evidence but do not ignore the image."""

IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT = "\n\n".join(
    [
        "You are Calry, an AI visual calorie-awareness assistant.",
        PRODUCT_PREAMBLE,
        "Objective: understand the meal in the image and return one realistic "
        "calorie estimate. Structured facts only, no chatty explanation.",
        OUTPUT_CONTRACT,
        _VISUAL_RULES,
        ESTIMATION_RULES,
        REFERENCE_ANCHORS,
        CONFIDENCE_AND_CLARIFICATION,
    ]
)


def build_image_meal_estimation_user_text(
    optional_hint: str | None = None,
    context: str = "",
    additional_context: str | None = None,
) -> str:
    prompt = "Task: analyze this food photo and estimate the meal calories."
    if optional_hint:
        prompt += f"\n\nUser hint:\n{optional_hint.strip()}"
    if additional_context and additional_context.strip():
        prompt += f"\n\nAdditional user context:\n{additional_context.strip()}"
    if context:
        prompt += f"\n\n{context.strip()}"
    prompt += "\n\nReturn the JSON object now."
    return prompt
