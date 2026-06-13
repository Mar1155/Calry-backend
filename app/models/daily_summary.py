import datetime as dt

from sqlalchemy import Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    date: Mapped[dt.date] = mapped_column(Date, index=True, nullable=False)
    consumed_calories: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    burned_calories: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    remaining_calories: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="daily_summaries")

    # Constraints: One summary per user per day
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_user_date"),)
