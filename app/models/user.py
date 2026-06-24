import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    firebase_uid: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    daily_calorie_goal: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    # goal_type: "lose" | "maintain" | "gain"
    goal_type: Mapped[str] = mapped_column(String(50), default="maintain", nullable=False)
    sex: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    # Premium Billing Fields
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    premium_entitlement: Mapped[str | None] = mapped_column(String(255), nullable=True)
    premium_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revenuecat_app_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Macro goals (premium feature)
    daily_protein_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_carbs_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_fat_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Push notification token (stored for future push support)
    fcm_token: Mapped[str | None] = mapped_column(String(512), nullable=True)

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

    # Relationships
    meals: Mapped[list["Meal"]] = relationship("Meal", back_populates="user", cascade="all, delete-orphan")
    burned_calories: Mapped[list["BurnedCalories"]] = relationship(
        "BurnedCalories", back_populates="user", cascade="all, delete-orphan"
    )
    daily_summaries: Mapped[list["DailySummary"]] = relationship(
        "DailySummary", back_populates="user", cascade="all, delete-orphan"
    )
    food_memories: Mapped[list["UserFoodMemory"]] = relationship(
        "UserFoodMemory", back_populates="user", cascade="all, delete-orphan"
    )
