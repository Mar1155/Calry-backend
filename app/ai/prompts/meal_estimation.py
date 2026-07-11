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

TEXT_MEAL_ESTIMATION_PROMPT_VERSION = "text_meal_estimation_v7"
JSON_REPAIR_PROMPT_VERSION = "json_repair_v2"

TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT = "\n\n".join(
    [
        "<role>You are Calry, a precise food-energy estimation engine.</role>",
        PRODUCT_PREAMBLE,
        "<task>Identify the consumed food and return one calibrated estimate of "
        "calories and macronutrients.</task>",
        OUTPUT_CONTRACT,
        EVIDENCE_POLICY,
        ESTIMATION_RULES,
        REFERENCE_ANCHORS,
        CONFIDENCE_AND_CLARIFICATION,
        INTERNAL_CHECK,
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
    prompt = f"""<input>
<meal_description>{escape(input_text.strip())}</meal_description>
"""
    if additional_context and additional_context.strip():
        prompt += (
            "<additional_user_context>"
            f"{escape(additional_context.strip())}"
            "</additional_user_context>\n"
        )
    if is_voice:
        prompt += "<input_source>automatic_voice_transcript</input_source>\n"
    if context:
        prompt += f"<user_context>{escape(context.strip())}</user_context>\n"
    prompt += """</input>
<task>
Based on the input above, estimate the meal. If this is a voice transcript,
silently tolerate filler words and plausible recognition errors, but do not
invent a food that was not mentioned. Return the required JSON object now.
</task>"""
    return prompt
