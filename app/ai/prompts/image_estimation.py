from xml.sax.saxutils import escape

from app.ai.prompts._shared import OUTPUT_CONTRACT

IMAGE_MEAL_ESTIMATION_PROMPT_VERSION = "image_meal_estimation_v12_item_boxes"

_FAT_LINE = '    "fat_g": number | null\n'
assert _FAT_LINE in OUTPUT_CONTRACT
# Photo-only addition to the shared contract: each item also carries its box.
_IMAGE_OUTPUT_CONTRACT = OUTPUT_CONTRACT.replace(
    _FAT_LINE,
    '    "fat_g": number | null,\n    "box_2d": [ymin, xmin, ymax, xmax] | null\n',
)

_VISUAL_RULES = """<rules>
Estimate only the main subject: the dish in the foreground that the photo is
framed on, usually central, in focus, and largest. Ignore food in the
background, blurred, partly cut off at the frame edges, or on other plates,
unless the user hint or context says it was eaten too. Use evidence in this
order: readable labels and explicit user facts; visible food and portion;
typical local serving data. Decompose every composite or prepared
dish into its calorie-bearing ingredients, one item per ingredient (for example,
pizza margherita becomes dough, tomato sauce, mozzarella, and olive oil). Put
the dish name only in meal_name, never as an item name. Keep a single item only
for a genuinely single-ingredient food or a branded product eaten as sold.
Estimate the edible amount consumed and represent each ingredient exactly once.
Item macros are for the full portion; weight and kcal/100g must use the
same cooked/raw state. Use realistic uncertainty bounds. Ask for clarification
only when no food or caloric drink can be identified. Never invent a brand,
recipe, preparation, or exact portion.
For each item, box_2d is a tight box around where that ingredient is visible in
the photo: integers 0-1000 relative to image height (y) and width (x), with
ymin < ymax and xmin < xmax. Use null when the ingredient cannot be seen on its
own (cooking oil, a sauce under everything, a hidden filling). Boxes locate
ingredients only; they never change weights or calories.
</rules>"""

IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT = "\n\n".join(
    [
        "<role>You are a visual food-energy estimation engine.</role>",
        "<task>Identify the food and estimate calories and macronutrients.</task>",
        _IMAGE_OUTPUT_CONTRACT,
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
