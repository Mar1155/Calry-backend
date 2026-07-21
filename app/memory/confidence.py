"""Deterministic, reproducible confidence for memories.

Confidence is a PURE function of the current evidence set, the domain, a
domain-supplied consistency scalar, and a reference time ``now``. There is no
irreversible accumulator: the same inputs always yield the same confidence, and
decay falls out of the recency term as ``now`` advances. Distillers recompute
from scratch on every pass.
"""

import datetime as dt
import math
from dataclasses import dataclass

# Explicit corrections carry at least as much information as passive confirmations.
SOURCE_WEIGHTS: dict[str, float] = {
    "correction": 1.0,
    "confirmation": 0.85,
    "meal": 0.7,
    "food_memory": 0.6,
    "pattern": 0.5,
}

# Confidence = structural strength * freshness. Strength grows with the quantity
# and quality of evidence; freshness is a multiplicative recency factor so a belief
# genuinely fades (and can archive) when it goes unreinforced — forgetting is real.
_W_SAMPLE = 0.30
_W_SPAN = 0.15
_W_SOURCE = 0.10
_W_CONSISTENCY = 0.10
_BASE = 0.30  # strength floors at 0.30 and caps at 0.95 before freshness
_MAX = 0.99


@dataclass(frozen=True)
class DomainParams:
    n_saturate: int
    span_cap_days: int
    half_life_days: int
    min_evidence: int
    min_span_days: int
    min_distinct_days: int
    min_confidence: float


DOMAIN_PARAMS: dict[str, DomainParams] = {
    "portion_model": DomainParams(6, 120, 90, min_evidence=4, min_span_days=14, min_distinct_days=3, min_confidence=0.50),
    "preference": DomainParams(8, 180, 120, min_evidence=4, min_span_days=14, min_distinct_days=3, min_confidence=0.50),
    "ai_calibration": DomainParams(10, 90, 30, min_evidence=6, min_span_days=14, min_distinct_days=4, min_confidence=0.50),
}


@dataclass(frozen=True)
class EvidenceItem:
    """A single unit of evidence. ``observed_day`` enables distinct-day counting."""

    evidence_type: str
    observed_at: dt.datetime
    observed_day: dt.date | None = None

    @property
    def weight(self) -> float:
        return SOURCE_WEIGHTS.get(self.evidence_type, 0.5)


@dataclass(frozen=True)
class EvidenceStats:
    count: int
    span_days: int
    distinct_days: int
    last_observed_at: dt.datetime | None


def summarize_evidence(items: list[EvidenceItem]) -> EvidenceStats:
    if not items:
        return EvidenceStats(count=0, span_days=0, distinct_days=0, last_observed_at=None)
    times = [item.observed_at for item in items]
    days = {item.observed_day for item in items if item.observed_day is not None}
    if not days:
        days = {t.date() for t in times}
    span = (max(times) - min(times)).days
    return EvidenceStats(count=len(items), span_days=max(0, span), distinct_days=len(days), last_observed_at=max(times))


def confidence_from_evidence(
    items: list[EvidenceItem],
    domain: str,
    *,
    consistency: float,
    now: dt.datetime,
) -> float:
    """Pure confidence in [0, 0.99]. ``consistency`` is a domain-supplied agreement
    scalar in [0, 1] (e.g. tightness of a portion spread)."""
    params = DOMAIN_PARAMS[domain]
    stats = summarize_evidence(items)
    if stats.count == 0 or stats.last_observed_at is None:
        return 0.0

    sample_score = min(1.0, stats.count / params.n_saturate)
    span_score = min(1.0, stats.span_days / params.span_cap_days) if params.span_cap_days > 0 else 1.0
    source_norm = sum(item.weight for item in items) / stats.count  # already in [0,1]
    consistency = max(0.0, min(1.0, consistency))

    strength = (
        _BASE
        + _W_SAMPLE * sample_score
        + _W_SPAN * span_score
        + _W_SOURCE * source_norm
        + _W_CONSISTENCY * consistency
    )

    age_days = max(0.0, (now - stats.last_observed_at).total_seconds() / 86400.0)
    freshness = math.exp(-age_days / params.half_life_days) if params.half_life_days > 0 else 1.0

    return round(max(0.0, min(_MAX, strength * freshness)), 4)


def passes_gate(
    items: list[EvidenceItem],
    domain: str,
    *,
    confidence: float,
) -> bool:
    """Materialization gate. A belief may exist only if every minimum is met."""
    params = DOMAIN_PARAMS[domain]
    stats = summarize_evidence(items)
    return (
        stats.count >= params.min_evidence
        and stats.span_days >= params.min_span_days
        and stats.distinct_days >= params.min_distinct_days
        and confidence >= params.min_confidence
    )
