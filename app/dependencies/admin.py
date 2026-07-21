import datetime as dt
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import CalryException, ForbiddenException
from app.core.security import verify_firebase_token
from app.dependencies.auth import security
from app.dependencies.db import get_db
from app.models.admin import AdminAuditLog
from app.services.privacy import pseudonymize


@dataclass(frozen=True)
class AdminIdentity:
    uid: str
    email: str | None


_rate_buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_last_audit_purge = 0.0


async def _purge_expired_audits(db: AsyncSession) -> None:
    global _last_audit_purge
    now = time.monotonic()
    if now - _last_audit_purge < 3600:
        return
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=settings.ADMIN_AUDIT_RETENTION_DAYS)
    await db.execute(delete(AdminAuditLog).where(AdminAuditLog.timestamp < cutoff))
    _last_audit_purge = now


def enforce_admin_rate_limit(request: Request, action: str, limit: int) -> None:
    """Small per-process guard; production edge/Redis limiting remains recommended."""
    host = request.client.host if request.client else "unknown"
    admin = getattr(request.state, "admin", None)
    principal = admin.uid if isinstance(admin, AdminIdentity) else host
    key = (principal, action)
    now = time.monotonic()
    bucket = _rate_buckets[key]
    while bucket and bucket[0] <= now - 60:
        bucket.popleft()
    if len(bucket) >= limit:
        raise CalryException("Too many admin requests. Try again shortly.", 429, "ADMIN_RATE_LIMITED")
    bucket.append(now)


async def get_current_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db),
) -> AdminIdentity:
    await _purge_expired_audits(db)
    payload = verify_firebase_token(credentials.credentials)
    uid = payload.get("uid")
    email = payload.get("email")
    if not uid:
        raise ForbiddenException("Administrator access denied.", "ADMIN_ACCESS_DENIED")

    claim_is_admin = payload.get("admin") is True
    allowlisted = uid in settings.admin_firebase_uids
    # Tests remain explicit: only tokens whose derived UID starts with admin are admins.
    test_admin = settings.is_testing and str(uid).startswith("admin")
    if not (claim_is_admin or allowlisted or test_admin):
        db.add(
            AdminAuditLog(
                admin_uid=str(uid),
                admin_email=None,
                action="admin_login_denied",
                result="denied",
                metadata_json={},
                request_id=getattr(request.state, "request_id", None),
                source_ip=pseudonymize(request.client.host if request.client else None, namespace="ip"),
            )
        )
        await db.commit()
        raise ForbiddenException("Administrator access denied.", "ADMIN_ACCESS_DENIED")

    identity = AdminIdentity(uid=str(uid), email=str(email) if email else None)
    request.state.admin = identity
    return identity


def new_audit(
    request: Request,
    admin: AdminIdentity,
    action: str,
    result: str,
    *,
    target_user_id: int | None = None,
    target_identifier: str | None = None,
    deletion_job_id: str | None = None,
    metadata: dict | None = None,
) -> AdminAuditLog:
    return AdminAuditLog(
        admin_uid=admin.uid,
        admin_email=None,
        action=action,
        target_user_id=target_user_id,
        safe_target_identifier=pseudonymize(target_identifier, namespace="target"),
        deletion_job_id=deletion_job_id,
        result=result,
        metadata_json=metadata or {},
        request_id=getattr(request.state, "request_id", None),
        source_ip=pseudonymize(request.client.host if request.client else None, namespace="ip"),
    )
