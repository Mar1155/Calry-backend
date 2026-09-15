import datetime as dt

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OnboardingEvent(Base):
    """Pseudonymous funnel events. Never store answers, meal text or raw errors."""

    __tablename__ = "onboarding_events"
    event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    journey_id: Mapped[str] = mapped_column(String(32), index=True)
    event_name: Mapped[str] = mapped_column(String(32))
    step: Mapped[str | None] = mapped_column(String(16), nullable=True)
    locale: Mapped[str] = mapped_column(String(2))
    platform: Mapped[str] = mapped_column(String(16))
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    onboarding_version: Mapped[int | None] = mapped_column(nullable=True)
    variant: Mapped[str | None] = mapped_column(String(32), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
