import hmac
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.dependencies.db import get_db
from app.services.revenuecat_webhook_service import RevenueCatWebhookService

logger = logging.getLogger("app.api.revenuecat_webhook")
router = APIRouter()


def _verify_webhook_authorization(authorization: str | None) -> None:
    """Validates the shared secret RevenueCat sends in the Authorization header.

    Accepts either the raw configured value or a ``Bearer <secret>`` form so
    the dashboard can be configured either way. Uses a constant-time compare.
    """
    secret = settings.REVENUECAT_WEBHOOK_SECRET
    if not secret:
        if settings.is_production:
            logger.error("revenuecat_webhook_rejected reason=secret_not_configured")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="RevenueCat webhook secret is not configured.",
            )
        logger.warning("revenuecat_webhook_auth_skipped reason=no_secret_configured environment=%s", settings.ENVIRONMENT)
        return

    provided = authorization or ""
    # Evaluate both forms before branching to keep the comparison constant-time.
    matches_bearer = hmac.compare_digest(provided, f"Bearer {secret}")
    matches_raw = hmac.compare_digest(provided, secret)
    if not (matches_bearer or matches_raw):
        logger.warning("revenuecat_webhook_rejected reason=invalid_authorization")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature credentials.",
        )


@router.post("/revenuecat", status_code=status.HTTP_200_OK)
async def process_revenuecat_webhook(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Receives and processes Server-to-Server billing events dispatched by RevenueCat."""
    _verify_webhook_authorization(authorization)
    service = RevenueCatWebhookService(db)
    return await service.process(payload)
