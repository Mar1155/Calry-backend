import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Reviewer verdicts for a scan (C27). "correct" is the only passing verdict;
# the rest name the failure so reviewed scans double as a labelled eval set.
SCAN_REVIEW_VERDICTS = (
    "correct",
    "calories_off",
    "portion_off",
    "not_decomposed",
    "missed_ingredient",
    "hallucinated_ingredient",
    "wrong_food",
    "other",
)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class ScanReview(Base):
    """One admin verdict per logged scan. Re-reviewing replaces the verdict."""

    __tablename__ = "scan_reviews"
    __table_args__ = (UniqueConstraint("inference_log_id", name="uq_scan_review_log"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inference_log_id: Mapped[int] = mapped_column(
        ForeignKey("ai_inference_logs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Denormalized so the user-deletion job can remove reviews directly.
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    verdict: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    # What the reviewer believes the meal really was, for building eval targets.
    expected_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_admin_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
