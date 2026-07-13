import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class PromoCode(Base):
    """A server-managed promotion; plaintext codes are deliberately not stored."""

    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    code_hint: Mapped[str] = mapped_column(String(24), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="free_access", nullable=False)
    # Reserved for the future "discounted_offering" kind. Store discounts
    # must resolve to a RevenueCat/store offering, never a client-side price.
    offering_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    grant_duration: Mapped[str] = mapped_column(String(32), default="lifetime", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redemption_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_from: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    redemptions: Mapped[list["PromoCodeRedemption"]] = relationship("PromoCodeRedemption", back_populates="promo_code")


class PromoCodeRedemption(Base):
    """Idempotent audit record for a user's RevenueCat promotional grant."""

    __tablename__ = "promo_code_redemptions"
    __table_args__ = (UniqueConstraint("promo_code_id", "user_id", name="uq_promo_code_redemption_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promo_code_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id", ondelete="RESTRICT"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    revenuecat_app_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    grant_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    promo_code: Mapped[PromoCode] = relationship("PromoCode", back_populates="redemptions")
    user: Mapped["User"] = relationship("User", back_populates="promo_code_redemptions")


class PromoCodeAttempt(Base):
    """Small audit/rate-limit record; stores only the HMAC digest attempted."""

    __tablename__ = "promo_code_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="promo_code_attempts")
