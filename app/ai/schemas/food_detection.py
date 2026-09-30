"""Food region detection (C28) contracts.

``DetectedRegion`` is the client-facing shape: coordinates are normalised to
0-1 of the (EXIF-upright) image, so the app maps them onto whatever frame it
renders without knowing the pixel size the model saw.
"""

from pydantic import BaseModel, Field


class DetectedRegion(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    ymin: float = Field(ge=0, le=1)
    xmin: float = Field(ge=0, le=1)
    ymax: float = Field(ge=0, le=1)
    xmax: float = Field(ge=0, le=1)


class FoodDetectionResult(BaseModel):
    regions: list[DetectedRegion] = Field(default_factory=list)
    model_name: str
    prompt_version: str
    raw_output: str | None = None
    latency_ms: int | None = None
    token_usage: dict | None = None
    finish_reason: str | None = None


# LLM-facing response schema, in Gemini's native box_2d format.
FOOD_DETECTION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "detections": {
            "type": "array",
            "description": "Visible food components, most prominent first.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Short ingredient name in the requested output language.",
                    },
                    "box_2d": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                        "description": "[ymin, xmin, ymax, xmax] normalised to 0-1000.",
                    },
                },
                "required": ["label", "box_2d"],
            },
        }
    },
    "required": ["detections"],
}
