"""AI Memory System models (Phase 0-1, deterministic MVP).

Two primitives: a *belief* is the living, mutable current model of one thing
Calry believes about a user; a *moment* is an immutable narrative event a belief
emits as it forms, changes, or returns. Evidence rows make every belief/moment
explainable; suppressions implement "forget this" (block re-derivation).

Confidence is never stored as an irreversible accumulator: it is a pure function
of the evidence set and a reference time, recomputed on every distillation pass.
The ``confidence`` column is a materialized cache from the last pass.
"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class MemoryBelief(Base):
    __tablename__ = "memory_beliefs"
    __table_args__ = (
        UniqueConstraint("user_id", "domain", "concept", "concept_key", name="uq_memory_belief_identity"),
        Index("ix_memory_beliefs_active", "user_id", "status", "domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    # Taxonomy. domain in {portion_model, preference, ai_calibration}; concept is a
    # domain-specific label; concept_key disambiguates (e.g. canonical food key or
    # calibration scope).
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    concept: Mapped[str] = mapped_column(String(60), nullable=False)
    concept_key: Mapped[str] = mapped_column(String(160), default="", server_default="", nullable=False)

    # Living state. status in {provisional, active, evolving, disputed, archived}.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Versioned, domain-specific payload (see app/memory/value_schemas.py).
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    value_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # Domain-specific agreement scalar persisted so confidence stays reproducible
    # from (evidence + consistency + now) on consolidation, without re-derivation.
    consistency: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Lifecycle (derived from evidence; kept for cheap querying).
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_reinforced_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_span_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # "Not correct" feedback (recoverable, distinct from suppression).
    dispute_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disputed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Provenance / versioning (mirrors InsightSnapshot conventions).
    distiller_id: Mapped[str] = mapped_column(String(80), nullable=False)
    distiller_version: Mapped[str] = mapped_column(String(40), nullable=False)
    source_versions_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict, nullable=False)

    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user: Mapped["User"] = relationship("User")
    revisions: Mapped[list["MemoryBeliefRevision"]] = relationship(
        "MemoryBeliefRevision", back_populates="belief", cascade="all, delete-orphan"
    )
    evidence: Mapped[list["MemoryEvidence"]] = relationship(
        "MemoryEvidence", back_populates="belief", cascade="all, delete-orphan"
    )
    moments: Mapped[list["MemoryMoment"]] = relationship("MemoryMoment", back_populates="belief")


class MemoryBeliefRevision(Base):
    __tablename__ = "memory_belief_revisions"
    __table_args__ = (Index("ix_memory_belief_revisions_belief", "belief_id", "revision_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    belief_id: Mapped[int] = mapped_column(ForeignKey("memory_beliefs.id", ondelete="CASCADE"), index=True, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    from_value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    to_value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    from_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    to_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # reinforcement | value_change | contradiction | dispute | decay | status_change
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    belief: Mapped["MemoryBelief"] = relationship("MemoryBelief", back_populates="revisions")


class MemoryMoment(Base):
    __tablename__ = "memory_moments"
    __table_args__ = (
        # One narrative event per belief per beat prevents duplicate cards. Milestone
        # moments have belief_id NULL, so beat_key alone carries their uniqueness via
        # the (user_id, beat_key) index used by the upsert path.
        UniqueConstraint("belief_id", "beat_key", name="uq_memory_moment_beat"),
        Index("ix_memory_moments_timeline", "user_id", "occurred_on", "moment_kind"),
        Index("ix_memory_moments_user_beat", "user_id", "beat_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    belief_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_beliefs.id", ondelete="SET NULL"), index=True, nullable=True
    )

    # discovery | learning | evolution | milestone | calibration
    moment_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    beat_key: Mapped[str] = mapped_column(String(80), nullable=False)

    # Deterministic, evidence-pinned facts the narrator may verbalize (and nothing else).
    fact_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence_at: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_span_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    occurred_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    chapter_key: Mapped[str] = mapped_column(String(12), nullable=False)  # "2026-07"

    distiller_version: Mapped[str] = mapped_column(String(40), nullable=False)
    # Set when the owning belief is forgotten; hidden from the timeline.
    hidden_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user: Mapped["User"] = relationship("User")
    belief: Mapped["MemoryBelief | None"] = relationship("MemoryBelief", back_populates="moments")
    narratives: Mapped[list["MemoryNarrative"]] = relationship(
        "MemoryNarrative", back_populates="moment", cascade="all, delete-orphan"
    )


class MemoryEvidence(Base):
    __tablename__ = "memory_evidence"
    __table_args__ = (
        # A given source row contributes at most once to a belief.
        UniqueConstraint("belief_id", "evidence_type", "ref_table", "ref_id", name="uq_memory_evidence_source"),
        Index("ix_memory_evidence_belief", "belief_id", "evidence_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    belief_id: Mapped[int] = mapped_column(ForeignKey("memory_beliefs.id", ondelete="CASCADE"), index=True, nullable=False)

    # meal | correction | confirmation | food_memory | pattern
    evidence_type: Mapped[str] = mapped_column(String(30), nullable=False)
    ref_table: Mapped[str] = mapped_column(String(40), nullable=False)
    ref_id: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    belief: Mapped["MemoryBelief"] = relationship("MemoryBelief", back_populates="evidence")


class MemoryNarrative(Base):
    __tablename__ = "memory_narratives"
    __table_args__ = (UniqueConstraint("moment_id", "locale", "prompt_version", name="uq_memory_narrative"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    moment_id: Mapped[int] = mapped_column(ForeignKey("memory_moments.id", ondelete="CASCADE"), index=True, nullable=False)
    locale: Mapped[str] = mapped_column(String(12), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), default="template", nullable=False)
    # Phase 1 is always "template"; "llm" is reserved for Phase 2.
    source: Mapped[str] = mapped_column(String(20), default="template", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    moment: Mapped["MemoryMoment"] = relationship("MemoryMoment", back_populates="narratives")


class MemorySuppression(Base):
    """A "forget this" request. Blocks re-derivation of the addressed belief."""

    __tablename__ = "memory_suppressions"
    __table_args__ = (
        UniqueConstraint("user_id", "domain", "concept", "concept_key", name="uq_memory_suppression_identity"),
        Index("ix_memory_suppressions_user", "user_id", "domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    concept: Mapped[str] = mapped_column(String(60), nullable=False)
    concept_key: Mapped[str] = mapped_column(String(160), default="", server_default="", nullable=False)
    # forget | manual
    reason: Mapped[str] = mapped_column(String(30), default="forget", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user: Mapped["User"] = relationship("User")
