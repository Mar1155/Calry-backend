import datetime as dt

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.local_day import track_local_date


class BurnedCalories(Base):
    __tablename__ = "burned_calories"
    __table_args__ = (Index("ix_burned_calories_user_local_date", "user_id", "local_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    activity_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calories: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )
    # The user's calendar day, fixed in their timezone (see app.core.client_time).
    local_date: Mapped[dt.date] = mapped_column(Date, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="burned_calories")


track_local_date(BurnedCalories)
