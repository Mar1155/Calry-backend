"""Shared dataclasses defining the distiller -> service contract."""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from app.memory.value_schemas import MemoryValueSchema


@dataclass(frozen=True)
class EvidenceRecord:
    """A typed piece of evidence to persist against a belief."""

    evidence_type: str  # meal | correction | confirmation | food_memory | pattern
    ref_table: str
    ref_id: int
    observed_at: dt.datetime
    weight: float = 1.0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MomentSpec:
    """A request to emit an immutable timeline moment (deduped by beat_key)."""

    moment_kind: str  # discovery | learning | evolution | milestone | calibration
    domain: str
    beat_key: str
    fact: dict[str, Any]
    confidence_at: float
    evidence_span_days: int
    occurred_on: dt.date


@dataclass(frozen=True)
class BeliefCandidate:
    """A distiller's proposal for one belief. The service computes confidence,
    applies the materialization gate, and manages lifecycle/moments."""

    domain: str
    concept: str
    concept_key: str
    value: MemoryValueSchema
    evidence: list[EvidenceRecord]
    consistency: float
    distiller_id: str
    distiller_version: str
    source_versions: dict[str, int] = field(default_factory=dict)
    # Moment to emit the first time this belief materializes (formation).
    formation_moment: MomentSpec | None = None
    # Further moments tied to this belief (e.g. calibration bands), emitted whenever
    # the belief is materialized, deduped by beat_key. Never intermediate-confidence
    # tiers — only meaningful, evidence-backed state changes.
    extra_moments: list[MomentSpec] = field(default_factory=list)


@dataclass(frozen=True)
class DistillationResult:
    belief_candidates: list[BeliefCandidate] = field(default_factory=list)
    # Moments not tied to a belief candidate (e.g. milestones).
    standalone_moments: list[MomentSpec] = field(default_factory=list)
