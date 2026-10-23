import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.app_version import normalize_platform

from app.dependencies.db import get_db
from app.schemas.app_setting import AppVersionResponse
from app.services.app_version_service import get_policy

logger = logging.getLogger("app.api.system")
router = APIRouter()


@router.get("/health", status_code=200)
async def health_check(db: AsyncSession = Depends(get_db)) -> dict:
    """Verifies that the API server is responsive and PostgreSQL is connected."""
    status = {"status": "healthy", "database": "connected"}
    try:
        # Perform quick low-overhead health query to check database connectivity
        await db.execute(select(1))
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        status["database"] = "disconnected"
        status["status"] = "degraded"
        # We still return status so the orchestrator (like Railway) gets details

    return status


@router.get("/app-version", response_model=AppVersionResponse)
async def app_version(
    request: Request,
    platform: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> AppVersionResponse:
    """Returns the effective version gate for the requesting platform."""
    resolved = normalize_platform(platform or request.headers.get("X-App-Platform"))
    policy = await get_policy(db)
    return AppVersionResponse(
        min_supported_app_version=policy.min_for(resolved),
        store_url=policy.store_url_for(resolved),
    )
