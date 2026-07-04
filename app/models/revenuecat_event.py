import datetime as dt

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RevenueCatEvent(Base):
    """Idempotency ledger and audit trail for every webhook event received from RevenueCat.

    ``event_id`` is unique: redelivered events are detected here and never
    produce duplicate side effects. The full raw payload is preserved for
    debugging and reprocessing.
    """

    __tablename__ = "revenuecat_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # RevenueCat's event UUID (or a payload hash when absent) — the idempotency key.
    event_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    app_user_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entitlement_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expiration_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    environment: Mapped[str | None] = mapped_column(String(50), nullable=True)
    store: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    # received | processed | failed | user_not_found | needs_reconciliation | ignored
    processing_status: Mapped[str] = mapped_column(String(50), default="received", nullable=False)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RevenueCatSubscriberSnapshot(Base):
    """Point-in-time copy of a subscriber's state fetched from the RevenueCat REST API.

    Each webhook that triggers an API verification stores the response here,
    so any historical premium decision can be audited against the exact
    subscriber state it was based on.
    """

    __tablename__ = "revenuecat_subscriber_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_user_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    entitlement_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )
