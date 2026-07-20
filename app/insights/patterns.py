from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VerifiedPattern(BaseModel):
    """A machine-verified fact. Detectors must never put prose in this model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=50)
    confidence: float = Field(ge=0, le=1)
    priority: int
    payload: dict[str, Any]

    # Ranking-only metadata. Excluded from the verified-pattern contract sent to
    # the LLM and from API responses.
    novelty: float = Field(default=0.5, ge=0, le=1, exclude=True)
    concept: str | None = Field(default=None, exclude=True)

    def verified_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "confidence": round(self.confidence, 4),
            "priority": self.priority,
            "payload": self.payload,
        }
