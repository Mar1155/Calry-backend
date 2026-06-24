import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.dependencies.db import get_db
from app.repositories.user import UserRepository

logger = logging.getLogger("app.api.revenuecat_webhook")
router = APIRouter()


@router.post("/revenuecat", status_code=status.HTTP_200_OK)
async def process_revenuecat_webhook(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Receives and processes Server-to-Server billing events dispatched by RevenueCat."""
    # 1. Validate Shared Secret if configured in env
    webhook_secret = getattr(settings, "REVENUECAT_WEBHOOK_SECRET", None)
    if webhook_secret:
        expected_auth = f"Bearer {webhook_secret}"
        if not authorization or authorization != expected_auth:
            logger.warning("Unauthorized access attempt on RevenueCat webhook endpoint.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature credentials.",
            )

    logger.info(f"Received RevenueCat Webhook event: {payload}")

    # 2. Extract Event parameters
    event_data = payload.get("event")
    if not event_data:
        logger.warning("Empty or malformed payload event received.")
        return {"status": "ignored", "reason": "No event object found"}

    event_type = event_data.get("type")
    app_user_id = event_data.get("app_user_id")
    entitlement_ids = event_data.get("entitlement_ids", [])
    expiration_ms = event_data.get("expiration_at_ms")

    if not app_user_id:
        logger.warning(f"RevenueCat event [{event_type}] contains no app_user_id.")
        return {"status": "ignored", "reason": "No app_user_id"}

    # 3. Locate matching user profile by Firebase UID (represented as RC app_user_id)
    user_repo = UserRepository(db)
    user = await user_repo.get_by_firebase_uid(app_user_id)
    if not user:
        logger.warning(f"User profile with firebase_uid={app_user_id} not found in database.")
        return {"status": "ignored", "reason": "User not found"}

    # Determine status & expiration details
    has_premium_entitlement = "premium" in entitlement_ids
    is_premium = False
    expires_dt = None

    if has_premium_entitlement:
        is_premium = True
        if expiration_ms:
            expires_dt = dt.datetime.fromtimestamp(expiration_ms / 1000.0, tz=dt.UTC)

    # If it is a cancel / expiration event, update accordingly
    if event_type in ("CANCELLATION", "EXPIRATION"):
        is_premium = False

    # 4. Save updated status
    await user_repo.update_user_premium_status(
        user=user,
        is_premium=is_premium,
        premium_entitlement="premium" if is_premium else None,
        premium_expires_at=expires_dt,
        revenuecat_app_user_id=app_user_id,
    )
    await db.commit()

    logger.info(f"Successfully processed webhook event [{event_type}] for user_id={user.id}. Premium active: {is_premium}")
    return {"status": "success", "event_processed": str(event_type)}
