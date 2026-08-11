import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

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
    __table_args__ = (Index("ix_detected_patterns_active", "user_id", "scope", "detector_id", "superseded_at"),)

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
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User")


class ProactiveInsightEvent(Base):
    """Transactional inbox item for event-driven or scheduled evaluation."""

    __tablename__ = "proactive_insight_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_proactive_insight_event_id"),
        Index("ix_proactive_insight_events_pending", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    trigger: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    affected_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    source_versions_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User")


class ProactiveInsight(Base):
    """Permanent, backend-owned Insight Diary record."""

    __tablename__ = "proactive_insights"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_proactive_insight_candidate"),
        Index("ix_proactive_insights_diary", "user_id", "created_at"),
        Index("ix_proactive_insights_dedup", "user_id", "dedup_key", "created_at"),
        Index("ix_proactive_insights_notification", "notification_status", "notification_ready_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    trigger: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    novelty: Mapped[float] = mapped_column(Float, nullable=False)
    relevance: Mapped[float] = mapped_column(Float, nullable=False)
    significance: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), default="neutral", nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    related_insight_id: Mapped[str | None] = mapped_column(
        ForeignKey("proactive_insights.id", ondelete="SET NULL"), nullable=True
    )
    notification_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    notification_status: Mapped[str] = mapped_column(String(20), default="not_eligible", index=True, nullable=False)
    notification_ready_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user: Mapped["User"] = relationship("User")


class InsightNotificationPreference(Base):
    __tablename__ = "insight_notification_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    proactive_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    daily_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weekly_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    quiet_hours_start: Mapped[str] = mapped_column(String(5), default="21:00", nullable=False)
    quiet_hours_end: Mapped[str] = mapped_column(String(5), default="08:00", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User")


class InsightNotificationDelivery(Base):
    __tablename__ = "insight_notification_deliveries"
    __table_args__ = (
        UniqueConstraint("insight_id", name="uq_insight_notification_delivery_insight"),
        UniqueConstraint("idempotency_key", name="uq_insight_notification_delivery_key"),
        Index("ix_insight_notification_deliveries_due", "status", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    insight_id: Mapped[str] = mapped_column(
        ForeignKey("proactive_insights.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    scheduled_for: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(30), default="fcm", nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    suppression_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    insight: Mapped["ProactiveInsight"] = relationship("ProactiveInsight")
    user: Mapped["User"] = relationship("User")


class InsightAnalyticsEvent(Base):
    __tablename__ = "insight_analytics_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_insight_analytics_event_id"),
        Index("ix_insight_analytics_events_metrics", "event_name", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    insight_id: Mapped[str | None] = mapped_column(
        ForeignKey("proactive_insights.id", ondelete="SET NULL"), nullable=True
    )
    event_name: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
