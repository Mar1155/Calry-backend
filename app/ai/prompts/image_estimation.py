from xml.sax.saxutils import escape

from app.ai.prompts._shared import (
    CONFIDENCE_AND_CLARIFICATION,
    ESTIMATION_RULES,
    EVIDENCE_POLICY,
    INTERNAL_CHECK,
    OUTPUT_CONTRACT,
    PRODUCT_PREAMBLE,
    REFERENCE_ANCHORS,
)

IMAGE_MEAL_ESTIMATION_PROMPT_VERSION = "image_meal_estimation_v7"

_VISUAL_RULES = """<visual_rules>
- First decide whether edible food or a caloric drink is visible. If not, return
  the clarification state defined above and ask for another photo or a text
  description.
- Identify visible components before estimating portions. Use plate, bowl,
  utensils, packaging, and known objects as scale only when their size is
  reasonably standard. Account for depth, stacking, occlusion, and leftovers.
- A single photo does not reveal exact weight, recipe, oil, fillings, or hidden
  layers. Do not claim that it does. Use plausible defaults, disclose material
  ones, widen the range, and lower confidence.
- Treat readable labels and the user's explicit hint as higher-quality evidence
  than visual guessing. If a hint conflicts with the image, retain what is
  compatible and disclose the material conflict rather than ignoring either.
- Do not identify a specific brand, cut, sauce, or cooking method from appearance
  alone unless visually distinctive.
</visual_rules>"""

IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT = "\n\n".join(
    [
        "<role>You are Calry, a precise visual food-energy estimation engine.</role>",
        PRODUCT_PREAMBLE,
        "<task>Identify food in the attached image and return one calibrated "
        "estimate of calories and macronutrients.</task>",
        OUTPUT_CONTRACT,
        EVIDENCE_POLICY,
        _VISUAL_RULES,
        ESTIMATION_RULES,
        REFERENCE_ANCHORS,
        CONFIDENCE_AND_CLARIFICATION,
        INTERNAL_CHECK,
    ]
)


def build_image_meal_estimation_user_text(
    optional_hint: str | None = None,
    context: str = "",
    additional_context: str | None = None,
) -> str:
    prompt = "<input>\n<media>one_attached_food_image</media>"
    if optional_hint:
        prompt += f"\n<user_hint>{escape(optional_hint.strip())}</user_hint>"
    if additional_context and additional_context.strip():
        prompt += (
            "\n<additional_user_context>"
            f"{escape(additional_context.strip())}"
            "</additional_user_context>"
        )
    if context:
        prompt += f"\n<user_context>{escape(context.strip())}</user_context>"
    prompt += """
</input>
<task>Based on the input above and the attached image, estimate the meal and
return the required JSON object now.</task>"""
    return prompt
