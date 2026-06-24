import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.models.user import User
from app.schemas.premium import PremiumStatusResponse, PremiumSyncRequest
from app.services.premium_service import PremiumService

logger = logging.getLogger("app.api.premium")
router = APIRouter()


@router.post("/sync", response_model=PremiumStatusResponse, status_code=status.HTTP_200_OK)
async def sync_premium_status(
    payload: PremiumSyncRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PremiumStatusResponse:
    """Updates user premium subscription state using verified Firebase authentication."""
    logger.info(f"Syncing premium state for user_id={current_user.id}, is_premium={payload.is_premium}")
    service = PremiumService(db)
    updated_user = await service.sync_premium(current_user, payload)
    return PremiumStatusResponse(
        is_premium=updated_user.is_premium,
        entitlement=updated_user.premium_entitlement,
        expires_at=updated_user.premium_expires_at,
        source="backend",
    )


@router.get("/status", response_model=PremiumStatusResponse)
async def get_premium_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PremiumStatusResponse:
    """Retrieves current cached user subscription details from backend database."""
    service = PremiumService(db)
    return await service.get_premium_status(current_user)
