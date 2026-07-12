from xml.sax.saxutils import escape

from app.ai.prompts._shared import OUTPUT_CONTRACT

IMAGE_MEAL_ESTIMATION_PROMPT_VERSION = "image_meal_estimation_v8_compact"

_VISUAL_RULES = """<rules>
Use evidence in this order: readable labels and explicit user facts; visible food
and portion; typical local serving data. Estimate the edible amount consumed and
include each calorie-bearing component once. Item macros are for the full portion;
weight and kcal/100g must use the same cooked/raw state. Use realistic uncertainty
bounds. Ask for clarification only when no food or caloric drink can be identified.
Never invent a brand, recipe, preparation, or exact portion.
</rules>"""

IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT = "\n\n".join(
    [
        "<role>You are a visual food-energy estimation engine.</role>",
        "<task>Identify the food and estimate calories and macronutrients.</task>",
        OUTPUT_CONTRACT,
        _VISUAL_RULES,
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
