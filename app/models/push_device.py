import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class PushDevice(Base):
    """One FCM registration token per installed app, owned by one account.

    A token is unique across accounts: signing into another account on the
    same phone moves the row instead of delivering to both people.
    ``users.fcm_token`` is kept as a legacy mirror of the newest token only so
    an older backend build can still deliver after a rollback.
    """

    __tablename__ = "push_devices"
    __table_args__ = (UniqueConstraint("token", name="uq_push_device_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token: Mapped[str] = mapped_column(String(512), nullable=False)
    # "ios" | "android" | None for clients that predate the field.
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user: Mapped["User"] = relationship("User")
