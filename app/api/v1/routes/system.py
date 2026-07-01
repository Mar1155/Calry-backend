import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.dependencies.db import get_db
from app.models.app_setting import AppSetting
from app.schemas.app_setting import AppVersionResponse

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
async def app_version(db: AsyncSession = Depends(get_db)) -> AppVersionResponse:
    """Returns server-side app version gate settings."""
    result = await db.execute(select(AppSetting).where(AppSetting.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        return AppVersionResponse(min_supported_app_version="0.0.1")

    return AppVersionResponse.model_validate(settings)
