import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Meal(Base):
    __tablename__ = "meals"
    __table_args__ = (
        # Idempotency (C13): a client request id is unique per user. Multiple NULLs
        # are allowed, so legacy/keyless logs are unaffected.
        UniqueConstraint("user_id", "client_request_id", name="uq_meal_user_request"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    # source_type: "text" | "voice" | "photo"
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    original_input: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    audio_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    meal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    estimated_calories: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_min_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_max_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimation_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correction_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correction_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_confidence: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_clarification: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    clarifying_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="meals")
    items: Mapped[list["MealItem"]] = relationship(
        "MealItem",
        back_populates="meal",
        cascade="all, delete-orphan",
        lazy="selectin",  # Preloads items asynchronously to avoid lazy loading issues
    )


class MealItem(Base):
    __tablename__ = "meal_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meal_id: Mapped[int] = mapped_column(ForeignKey("meals.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity_estimate: Mapped[str | None] = mapped_column(String(100), nullable=True)
    weight_grams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calories_per_100g: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )

    # Relationships
    meal: Mapped["Meal"] = relationship("Meal", back_populates="items")

    @property
    def estimated_calories(self) -> int:
        if self.weight_grams is None or self.calories_per_100g is None:
            return 0
        return max(0, int(round(self.weight_grams * self.calories_per_100g / 100)))
