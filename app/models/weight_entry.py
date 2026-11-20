import datetime as dt

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class WeightEntry(Base):
    """One body-weight measurement per user per calendar day.

    A day holds a single value: logging again on the same day replaces it.
    ``users.weight_kg`` mirrors the most recent entry so the calorie formula
    and insights keep reading one current weight.
    """

    __tablename__ = "weight_entries"
    __table_args__ = (UniqueConstraint("user_id", "local_date", name="uq_weight_entry_user_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    # The user's calendar day, fixed in their timezone (see app.core.client_time).
    local_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    # "manual" | "onboarding" | "profile"
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
        nullable=True,
    )

    user: Mapped["User"] = relationship("User", back_populates="weight_entries")
