import logging
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.app_version import compare_versions, is_valid_version
from app.models.app_setting import AppSetting

logger = logging.getLogger("app.app_version")

DEFAULT_MIN_VERSION = "0.0.1"
_CACHE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class VersionPolicy:
    min_version: str
    min_ios: str | None
    min_android: str | None
    ios_store_url: str | None
    android_store_url: str | None

    def min_for(self, platform: str | None) -> str:
        override = {"ios": self.min_ios, "android": self.min_android}.get(platform or "")
        return override or self.min_version

    def store_url_for(self, platform: str | None) -> str | None:
        return {"ios": self.ios_store_url, "android": self.android_store_url}.get(platform or "")


_cache: tuple[float, VersionPolicy] | None = None


def invalidate_policy_cache() -> None:
    global _cache
    _cache = None


def _safe_version(value: str | None, field: str) -> str | None:
    """Reject malformed stored values loudly instead of silently disabling the gate."""
    if value is None:
        return None
    if is_valid_version(value):
        return value.strip()
    logger.error("app_version_policy_malformed field=%s value=%r — ignoring", field, value)
    return None


async def get_policy(db: AsyncSession) -> VersionPolicy:
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]

    row = (await db.execute(select(AppSetting).where(AppSetting.id == 1))).scalar_one_or_none()
    if row is None:
        policy = VersionPolicy(DEFAULT_MIN_VERSION, None, None, None, None)
    else:
        policy = VersionPolicy(
            min_version=_safe_version(row.min_supported_app_version, "min_supported_app_version") or DEFAULT_MIN_VERSION,
            min_ios=_safe_version(row.min_supported_ios_version, "min_supported_ios_version"),
            min_android=_safe_version(row.min_supported_android_version, "min_supported_android_version"),
            ios_store_url=row.ios_store_url,
            android_store_url=row.android_store_url,
        )
    _cache = (now, policy)
    return policy


def is_supported(installed: str, minimum: str) -> bool:
    return compare_versions(installed, minimum) >= 0
