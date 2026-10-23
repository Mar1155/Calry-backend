import logging

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_version import is_valid_version, normalize_platform
from app.core.exceptions import CalryException
from app.dependencies.db import get_db
from app.services.app_version_service import get_policy, is_supported

logger = logging.getLogger("app.app_version")


async def enforce_min_app_version(request: Request, db: AsyncSession = Depends(get_db)) -> None:
    """Reject clients older than the server-side minimum with HTTP 426.

    Clients identify themselves with `X-App-Version` / `X-App-Platform`. Requests
    without a valid pair (webhooks, tooling, pre-header builds) are not gated —
    the in-app startup check still covers those builds.
    """
    version = (request.headers.get("X-App-Version") or "").strip().split("+", 1)[0]
    platform = normalize_platform(request.headers.get("X-App-Platform"))
    if platform is None or not is_valid_version(version.split("-", 1)[0]):
        return

    policy = await get_policy(db)
    minimum = policy.min_for(platform)
    if is_supported(version, minimum):
        return

    logger.info("app_version_rejected platform=%s version=%s minimum=%s", platform, version, minimum)
    raise CalryException(
        "This version of the app is no longer supported. Please update.",
        426,
        "UPGRADE_REQUIRED",
        details={"min_supported_app_version": minimum, "store_url": policy.store_url_for(platform)},
    )
