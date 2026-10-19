import datetime as dt

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserAiUsage(Base):
    """One row per AI meal analysis charged against a free user's daily quota.

    A row per request (rather than a single counter) makes charging idempotent:
    a retry or reconnect with the same request key finds its existing row
    instead of consuming a second credit, and a failed or cancelled analysis
    is refunded by deleting exactly its own row. The day's usage is the number
    of rows for that date.
    """

    __tablename__ = "user_ai_usage"
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_user_ai_usage_request"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    usage_date: Mapped[dt.date] = mapped_column(Date, index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    request_key: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )
