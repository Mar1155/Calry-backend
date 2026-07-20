import datetime as dt
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class UserInsightVersion(Base):
    __tablename__ = "user_insight_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    meal_data_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    activity_data_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    hydration_data_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    profile_data_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    target_data_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    weight_data_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    ai_accuracy_data_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    logging_behavior_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user: Mapped["User"] = relationship("User")

    def as_dict(self) -> dict[str, int]:
        return {
            "meal_data_version": self.meal_data_version,
            "activity_data_version": self.activity_data_version,
            "hydration_data_version": self.hydration_data_version,
            "profile_data_version": self.profile_data_version,
            "target_data_version": self.target_data_version,
            "weight_data_version": self.weight_data_version,
            "ai_accuracy_data_version": self.ai_accuracy_data_version,
            "logging_behavior_version": self.logging_behavior_version,
        }


class DetectedPattern(Base):
    __tablename__ = "detected_patterns"
    __table_args__ = (
        Index("ix_detected_patterns_active", "user_id", "scope", "detector_id", "superseded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    detector_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    detector_version: Mapped[str] = mapped_column(String(40), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    period_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    source_versions_json: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    pattern_key: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    novelty_score: Mapped[float] = mapped_column(Float, nullable=False)
    effect_size: Mapped[float] = mapped_column(Float, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list, nullable=False)
    comparison_status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    stale_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User")


class InsightSnapshot(Base):
    __tablename__ = "insight_snapshots"
    __table_args__ = (
        UniqueConstraint("generation_key", name="uq_insight_snapshot_generation_key"),
        Index("ix_insight_snapshots_latest", "user_id", "insight_scope", "locale", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    generation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    insight_scope: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    locale: Mapped[str] = mapped_column(String(12), default="en", nullable=False)
    period_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    source_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_versions_json: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    detector_version: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    stale_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    insights_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    ranking_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user: Mapped["User"] = relationship("User")
