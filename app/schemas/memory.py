"""API schemas for the AI Memory System (read-only + feedback actions)."""

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class MemoryEvidenceEntry(BaseModel):
    evidence_type: str
    ref_table: str
    ref_id: int
    observed_at: dt.datetime
    detail: dict[str, Any] = Field(default_factory=dict)


class MemoryWhy(BaseModel):
    """Evidence-backed justification. Every displayed moment/belief carries one."""

    evidence_count: int = 0
    span_days: int = 0
    distinct_days: int | None = None
    first_seen: dt.datetime | None = None
    last_reinforced: dt.datetime | None = None
    source_breakdown: dict[str, int] = Field(default_factory=dict)
    evidence: list[MemoryEvidenceEntry] = Field(default_factory=list)
    # Plain-language basis for deterministic milestones (no belief evidence rows).
    explanation: str | None = None


class MemoryMomentResponse(BaseModel):
    id: int
    moment_kind: str
    domain: str
    beat_key: str
    text: str
    confidence_at: float
    evidence_span_days: int
    occurred_on: dt.date
    chapter_key: str
    belief_id: int | None = None
    why: MemoryWhy


class MemoryTimelineChapter(BaseModel):
    chapter_key: str
    moments: list[MemoryMomentResponse] = Field(default_factory=list)


class MemoryTimelineResponse(BaseModel):
    chapters: list[MemoryTimelineChapter] = Field(default_factory=list)
    next_cursor: str | None = None


class MemoryLatestResponse(BaseModel):
    moment: MemoryMomentResponse | None = None


class MemoryBeliefResponse(BaseModel):
    id: int
    domain: str
    concept: str
    concept_key: str
    status: str
    value: dict[str, Any]
    confidence: float
    statement: str
    first_seen_at: dt.datetime
    last_reinforced_at: dt.datetime
    evidence_span_days: int
    observation_count: int
    dispute_count: int


class MemoryRevisionResponse(BaseModel):
    revision_no: int
    reason: str
    from_value: dict[str, Any]
    to_value: dict[str, Any]
    from_confidence: float
    to_confidence: float
    created_at: dt.datetime


class MemoryBeliefDetailResponse(BaseModel):
    belief: MemoryBeliefResponse
    why: MemoryWhy
    revisions: list[MemoryRevisionResponse] = Field(default_factory=list)


class MemoryBeliefsResponse(BaseModel):
    beliefs: list[MemoryBeliefResponse] = Field(default_factory=list)


class MemoryActionResponse(BaseModel):
    ok: bool
    detail: str
