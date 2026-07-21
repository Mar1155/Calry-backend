import datetime as dt
import uuid

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class UserDeletionJob(Base):
    __tablename__ = "user_deletion_jobs"
    __table_args__ = (
        Index("ix_user_deletion_jobs_target_status", "target_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: f"del_{uuid.uuid4().hex}")
    target_user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    target_email: Mapped[str] = mapped_column(String(255), nullable=False)
    target_firebase_uid: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    target_revenuecat_app_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_by_admin_uid: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    requested_by_admin_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preview_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    steps_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: f"aud_{uuid.uuid4().hex}")
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    admin_uid: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    admin_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    safe_target_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deletion_job_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
