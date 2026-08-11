import json

from pydantic import BaseModel, ConfigDict, Field

from app.ai.providers.openrouter import OpenRouterProvider
from app.core.config import settings
from app.proactive_insights.candidates import InsightCandidate

PROACTIVE_INSIGHT_PROMPT_VERSION = "proactive_insight_v1_verified_candidate"

PROACTIVE_INSIGHT_SYSTEM_PROMPT = """You are Calry's concise communication layer. Input is one candidate verified by deterministic backend code. Write personalized copy using only input evidence and metrics.

Never calculate metrics. Never add facts, numbers, patterns, advice, causality, or medical claims. Never reverse direction. Never moralize food or judge the user. Copy numbers only when they appear exactly in input. Preserve candidate_id and direction exactly.

Return strict JSON only:
{"candidate_id":"","direction":"positive|negative|neutral","title":"","body":"","evidence_refs":["metrics.field_name"]}

Rules:
- title: at most 6 words and 80 characters
- body: at most 2 short sentences and 360 characters
- no markdown
- no fields beyond schema
- prefer silence over padding"""


class GeneratedInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=16, max_length=64)
    direction: str = Field(pattern="^(positive|negative|neutral)$")
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=360)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class ProactiveInsightVerbalizer:
    def __init__(self, provider: OpenRouterProvider | None = None):
        self.provider = provider or OpenRouterProvider()

    async def verbalize(self, candidate: InsightCandidate, *, locale: str = "en") -> GeneratedInsight:
        payload = {
            "output_language": self.provider._insight_language(locale),
            "verified_candidate": candidate.verified_dict(),
        }
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["candidate_id", "direction", "title", "body", "evidence_refs"],
            "properties": {
                "candidate_id": {"type": "string"},
                "direction": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "evidence_refs": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {"type": "string"},
                },
            },
        }
        text, _, _ = await self.provider._post_openrouter(
            model=settings.PROACTIVE_INSIGHT_MODEL,
            system_prompt=PROACTIVE_INSIGHT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, separators=(",", ":"))}],
            response_format=self.provider._response_format(schema, "proactive_insight"),
        )
        generated = GeneratedInsight.model_validate_json(self.provider._extract_json(text))
        if generated.candidate_id != candidate.candidate_id or generated.direction != candidate.direction:
            raise ValueError("Generated insight changed immutable candidate fields.")
        if len(generated.title.split()) > 6:
            raise ValueError("Generated insight title exceeds six words.")
        return generated
