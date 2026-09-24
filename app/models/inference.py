import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AIInferenceLog(Base):
    __tablename__ = "ai_inference_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    input_type: Mapped[str] = mapped_column(String(50), nullable=False)  # text, voice, photo
    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # C26: the provider's raw finish_reason ("stop", "length", ...). "length"
    # means the completion was cut off by max_completion_tokens — queryable
    # signal for how often meal estimates are getting truncated.
    finish_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Scan audit (C27). The meal this call produced or revised, so an admin can
    # go from a logged call to the persisted result and back. SET NULL keeps the
    # log (and its review) if the user deletes the meal.
    meal_id: Mapped[int | None] = mapped_column(
        ForeignKey("meals.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # Post-validation quality signals, captured at log time because they are
    # not persisted on the meal. Columns (not JSON) where the audit filters.
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    degraded_extraction: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    needs_clarification: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Remaining flags: density_clamped, macro_mismatch, total_realigned,
    # bias_applied, assumptions.
    quality_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Token usage (C2) — cost telemetry and cache-hit measurement.
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        index=True,
        nullable=False,
    )

    # Relationships
    user: Mapped["User | None"] = relationship("User")
