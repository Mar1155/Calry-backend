import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.user import User
from app.schemas.premium import (
    PremiumStatusResponse,
    PremiumSyncRequest,
    PromoCodeRedeemRequest,
    PromoCodeRedeemResponse,
)
from app.services.premium_service import PremiumService
from app.services.promo_code_service import PromoCodeService
from app.services.revenuecat_service import RevenueCatAPIError

logger = logging.getLogger("app.api.premium")
router = APIRouter()


@router.post("/sync", response_model=PremiumStatusResponse, status_code=status.HTTP_200_OK)
async def sync_premium_status(
    payload: PremiumSyncRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PremiumStatusResponse:
    """Updates user premium subscription state using verified Firebase authentication."""
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Premium status is managed by RevenueCat webhooks in production.",
        )

    logger.info(f"Syncing premium state for user_id={current_user.id}, is_premium={payload.is_premium}")
    service = PremiumService(db)
    updated_user = await service.sync_premium(current_user, payload)
    return PremiumStatusResponse(
        is_premium=updated_user.is_premium,
        entitlement=updated_user.premium_entitlement,
        expires_at=updated_user.premium_expires_at,
        source="backend",
        store=updated_user.premium_store,
        product_id=updated_user.premium_product_id,
        last_verified_at=updated_user.premium_last_verified_at,
    )


@router.get("/status", response_model=PremiumStatusResponse)
async def get_premium_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PremiumStatusResponse:
    """Retrieves current cached user subscription details from backend database."""
    service = PremiumService(db)
    return await service.get_premium_status(current_user)


@router.post("/refresh", response_model=PremiumStatusResponse)
async def refresh_premium_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PremiumStatusResponse:
    """Refreshes access from RevenueCat using the authenticated Firebase UID."""
    try:
        return await PremiumService(db).refresh_from_revenuecat(current_user)
    except RevenueCatAPIError as exc:
        logger.warning("premium_refresh_failed user_id=%s error=%s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Premium status could not be refreshed right now.",
        ) from exc


@router.post("/redeem-code", response_model=PromoCodeRedeemResponse)
async def redeem_promo_code(
    payload: PromoCodeRedeemRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PromoCodeRedeemResponse:
    """Redeems an opaque, server-managed free-access code exactly once."""
    return await PromoCodeService(db).redeem_free_access(current_user, payload.code)
