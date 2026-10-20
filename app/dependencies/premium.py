import datetime as dt
import logging
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.ai_usage import UserAiUsage
from app.models.user import User
from app.services.premium_service import PremiumService
from app.services.revenuecat_service import RevenueCatAPIError

FREE_HISTORY_DAYS = 7
# AI meal analyses (photo and voice) a free account may start per UTC day.
# Text analysis and manual edits are not counted.
FREE_DAILY_AI_CREDITS = 3
PRO_AI_CREDITS: int | None = None  # unlimited

AI_QUOTA_EXCEEDED = "AI_QUOTA_EXCEEDED"
FEATURE_LOCKED_PRO = "FEATURE_LOCKED_PRO"
logger = logging.getLogger("app.dependencies.premium")


async def has_premium_access(user: User, db: AsyncSession) -> bool:
    """Resolves cached access consistently, including expiry and test bypass."""
    premium = await PremiumService(db).get_premium_status(user)
    return premium.is_premium


async def has_verified_premium_access(user: User, db: AsyncSession) -> bool:
    """Cached access, re-verified with RevenueCat before denying.

    Covers a purchase whose webhook has not reached us yet without ever
    trusting a client flag. Failed verification remains safely locked.
    """
    service = PremiumService(db)
    premium = await service.get_premium_status(user)
    if not premium.is_premium:
        try:
            premium = await service.refresh_from_revenuecat(user)
        except RevenueCatAPIError as exc:
            logger.info("premium_gate_refresh_unavailable user_id=%s error=%s", user.id, exc)
    return premium.is_premium


async def ensure_pro_feature(user: User, db: AsyncSession, feature: str) -> None:
    """Locks a single Pro feature with a machine-readable code the app can
    turn into a contextual paywall instead of a generic error."""
    if await has_verified_premium_access(user, db):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": FEATURE_LOCKED_PRO,
            "code": FEATURE_LOCKED_PRO,
            "feature": feature,
            "message": "Calory Pro is required for this feature.",
        },
    )


async def require_premium_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Server-side authorization dependency for every Calry Pro API."""
    if not await has_verified_premium_access(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Calry Premium subscription required for this feature.",
        )
    return current_user


def free_history_cutoff(today: dt.date | None = None) -> dt.date:
    return (today or dt.date.today()) - dt.timedelta(days=FREE_HISTORY_DAYS - 1)


async def ensure_history_date_access(
    date_value: dt.date,
    user: User,
    db: AsyncSession,
) -> None:
    """Free accounts may read and edit today plus the previous six days."""
    if date_value >= free_history_cutoff() or await has_verified_premium_access(user, db):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Calory Pro is required to access history older than 7 days.",
    )


@dataclass(frozen=True)
class AiQuotaStatus:
    unlimited: bool
    limit: int | None
    used: int
    remaining: int | None
    reset_at: dt.datetime


def _ai_usage_today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def _ai_quota_reset_at(today: dt.date) -> dt.datetime:
    return dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)


async def _ai_credits_used(db: AsyncSession, user_id: int, today: dt.date) -> int:
    result = await db.execute(
        select(func.count(UserAiUsage.id)).where(
            UserAiUsage.user_id == user_id,
            UserAiUsage.usage_date == today,
        )
    )
    return int(result.scalar_one())


async def get_ai_quota_status(user: User, db: AsyncSession) -> AiQuotaStatus:
    today = _ai_usage_today()
    reset_at = _ai_quota_reset_at(today)
    used = await _ai_credits_used(db, user.id, today)
    if await has_premium_access(user, db):
        return AiQuotaStatus(unlimited=True, limit=PRO_AI_CREDITS, used=used, remaining=None, reset_at=reset_at)
    return AiQuotaStatus(
        unlimited=False,
        limit=FREE_DAILY_AI_CREDITS,
        used=used,
        remaining=max(FREE_DAILY_AI_CREDITS - used, 0),
        reset_at=reset_at,
    )


def new_ai_request_key() -> str:
    return str(uuid.uuid4())


async def check_and_consume_ai_credit(
    user: User,
    db: AsyncSession,
    *,
    source_type: str,
    request_key: str,
) -> bool:
    """Charges one AI analysis to a free account, or raises 403 AI_QUOTA_EXCEEDED.

    Pro accounts are never charged. Idempotent per ``request_key`` (the
    client_request_id when the app sends one): a retry or reconnect of an
    already-charged request passes without consuming again. Commits, so the
    charge is durable before a long-running analysis starts.
    """
    if await has_premium_access(user, db):
        return True

    existing = await db.execute(
        select(UserAiUsage.id).where(UserAiUsage.user_id == user.id, UserAiUsage.request_key == request_key)
    )
    if existing.scalar_one_or_none() is not None:
        return True

    today = _ai_usage_today()
    used = await _ai_credits_used(db, user.id, today)
    if used >= FREE_DAILY_AI_CREDITS:
        # A purchase made seconds ago on the paywall may not have reached us
        # through the webhook yet.
        if await has_verified_premium_access(user, db):
            return True
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": AI_QUOTA_EXCEEDED,
                "code": AI_QUOTA_EXCEEDED,
                "limit": FREE_DAILY_AI_CREDITS,
                "remaining": 0,
                "reset_at": _ai_quota_reset_at(today).isoformat(),
                "message": "Daily free AI analyses used up.",
            },
        )

    try:
        async with db.begin_nested():
            db.add(UserAiUsage(user_id=user.id, usage_date=today, source_type=source_type, request_key=request_key))
    except IntegrityError:
        # A concurrent retry of the same request charged it first. Only the
        # savepoint is rolled back, so the caller's loaded objects stay valid.
        pass
    await db.commit()
    return True


async def refund_ai_credit(db: AsyncSession, user_id: int, request_key: str | None) -> None:
    """Returns the credit of an analysis that produced no meal (failed,
    timed out in the worker, or cancelled by the user). Commits."""
    if not request_key:
        return
    await db.execute(delete(UserAiUsage).where(UserAiUsage.user_id == user_id, UserAiUsage.request_key == request_key))
    await db.commit()
