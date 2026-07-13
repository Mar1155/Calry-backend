import datetime as dt
import logging

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.user import User
from app.services.premium_service import PremiumService
from app.services.revenuecat_service import RevenueCatAPIError

FREE_HISTORY_DAYS = 7
logger = logging.getLogger("app.dependencies.premium")


async def has_premium_access(user: User, db: AsyncSession) -> bool:
    """Resolves cached access consistently, including expiry and test bypass."""
    premium = await PremiumService(db).get_premium_status(user)
    return premium.is_premium


async def require_premium_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Server-side authorization dependency for every Calry Pro API."""
    service = PremiumService(db)
    premium = await service.get_premium_status(current_user)
    if not premium.is_premium:
        # Covers a purchase whose webhook has not reached us yet without ever
        # trusting a client flag. Failed verification remains safely locked.
        try:
            premium = await service.refresh_from_revenuecat(current_user)
        except RevenueCatAPIError as exc:
            logger.info("premium_gate_refresh_unavailable user_id=%s error=%s", current_user.id, exc)
    if not premium.is_premium:
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
    if date_value >= free_history_cutoff() or await has_premium_access(user, db):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Calry Pro is required to access history older than 7 days.",
    )
