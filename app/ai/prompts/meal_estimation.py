from app.ai.prompts._shared import (
    CONFIDENCE_AND_CLARIFICATION,
    ESTIMATION_RULES,
    OUTPUT_CONTRACT,
    PRODUCT_PREAMBLE,
    REFERENCE_ANCHORS,
)

TEXT_MEAL_ESTIMATION_PROMPT_VERSION = "text_meal_estimation_v5"
JSON_REPAIR_PROMPT_VERSION = "json_repair_v2"

TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT = "\n\n".join(
    [
        "You are Calry, an AI calorie-awareness assistant.",
        PRODUCT_PREAMBLE,
        "Objective: return one fast, realistic calorie estimate for the user's meal. "
        "Be useful, calm, and correction-friendly.",
        OUTPUT_CONTRACT,
        ESTIMATION_RULES,
        REFERENCE_ANCHORS,
        CONFIDENCE_AND_CLARIFICATION,
    ]
)

JSON_REPAIR_SYSTEM_PROMPT = """You are a JSON repair assistant.
Repair malformed model output into valid raw JSON matching the requested schema.
Preserve all nutritional facts when possible.
Do not add prose. Do not wrap in markdown fences.
Enum values must be lowercase ASCII exactly as listed.
"""


def build_text_meal_estimation_user_prompt(
    input_text: str,
    context: str = "",
    is_voice: bool = False,
    additional_context: str | None = None,
) -> str:
    prompt = f"""Task: estimate calories for this meal description.

Meal description:
{input_text.strip()}
"""
    if additional_context and additional_context.strip():
        prompt += f"\nAdditional user context:\n{additional_context.strip()}\n"
    if is_voice:
        prompt += (
            "\nNote: the description above is an automatic voice transcript and may "
            "contain speech-recognition errors, filler words, or run-on phrasing. "
            "Infer the intended meal and estimate it. Do not ask for clarification "
            "unless no food is mentioned at all.\n"
        )
    if context:
        prompt += f"\n{context.strip()}\n"
    prompt += "\nReturn the JSON object now."
    return prompt
