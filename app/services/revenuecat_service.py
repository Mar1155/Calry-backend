"""Isolated client for the RevenueCat REST API.

Provides subscriber fetches with retry/backoff and pure helpers to derive the
entitlement state used to decide ``user.is_premium``. No database access here.
"""

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger("app.services.revenuecat")

REVENUECAT_API_BASE_URL = "https://api.revenuecat.com/v1"
_RETRY_AFTER_CAP_SECONDS = 5.0


class RevenueCatAPIError(Exception):
    """Raised when the RevenueCat REST API cannot be reached or returns an error."""


@dataclass(frozen=True)
class EntitlementState:
    """Resolved premium entitlement state for a subscriber."""

    is_active: bool
    expires_at: dt.datetime | None
    product_id: str | None
    store: str | None


def _parse_rc_datetime(value: str | None) -> dt.datetime | None:
    """Parses RevenueCat ISO-8601 timestamps (which use a trailing ``Z``)."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("revenuecat_parse_error field=expires_date value=%s", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def derive_entitlement_state(
    subscriber_payload: dict[str, Any],
    entitlement_id: str,
    now: dt.datetime | None = None,
) -> EntitlementState:
    """Derives the active/inactive premium state from a GET /subscribers response.

    An entitlement is active when its ``expires_date`` is in the future or null
    (lifetime purchases). This is the single source of truth used to set
    ``user.is_premium`` — never the webhook event type.
    """
    now = now or dt.datetime.now(dt.UTC)
    subscriber = subscriber_payload.get("subscriber") or {}
    entitlement = (subscriber.get("entitlements") or {}).get(entitlement_id)
    if not entitlement:
        return EntitlementState(is_active=False, expires_at=None, product_id=None, store=None)

    expires_at = _parse_rc_datetime(entitlement.get("expires_date"))
    is_active = expires_at is None or expires_at > now
    product_id = entitlement.get("product_identifier")

    # The store lives on the subscription (or non-subscription purchase), keyed
    # by product id. Play Store keys may carry a ":base_plan" suffix.
    store: str | None = None
    subscriptions = subscriber.get("subscriptions") or {}
    entry = subscriptions.get(product_id)
    if entry is None and product_id:
        for key, value in subscriptions.items():
            if key.split(":")[0] == product_id:
                entry = value
                break
    if isinstance(entry, dict):
        store = entry.get("store")
    if store is None and product_id:
        purchases = (subscriber.get("non_subscriptions") or {}).get(product_id) or []
        if purchases and isinstance(purchases[0], dict):
            store = purchases[0].get("store")
    if store is None and isinstance(product_id, str) and product_id.startswith("rc_promo"):
        store = "promotional"

    return EntitlementState(is_active=is_active, expires_at=expires_at, product_id=product_id, store=store)


class RevenueCatClient:
    """Thin async client for the RevenueCat v1 REST API with retry/backoff."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ):
        self.api_key = api_key if api_key is not None else settings.REVENUECAT_API_KEY
        self.timeout_seconds = timeout_seconds or settings.REVENUECAT_API_TIMEOUT_SECONDS
        self.max_retries = max_retries if max_retries is not None else settings.REVENUECAT_API_MAX_RETRIES

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def get_subscriber(self, app_user_id: str) -> dict[str, Any]:
        """Fetches the current subscriber state for ``app_user_id``.

        Retries transient failures (network errors, 429, 5xx) with exponential
        backoff, honoring ``Retry-After`` when present. Raises
        :class:`RevenueCatAPIError` when every attempt fails or the API
        responds with a non-retryable error.
        """
        if not self.is_configured:
            raise RevenueCatAPIError("REVENUECAT_API_KEY is not configured.")

        url = f"{REVENUECAT_API_BASE_URL}/subscribers/{quote(app_user_id, safe='')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

        last_error: str = "unknown error"
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"network error: {type(exc).__name__}"
                logger.warning(
                    "revenuecat_api_error attempt=%d app_user_id=%s error=%s",
                    attempt + 1,
                    app_user_id,
                    last_error,
                )
            else:
                if response.status_code == 200:
                    return response.json()
                last_error = f"HTTP {response.status_code}"
                if response.status_code != 429 and response.status_code < 500:
                    # Client errors (401/403/404) will not resolve on retry.
                    logger.error(
                        "revenuecat_api_error attempt=%d app_user_id=%s error=%s retryable=false",
                        attempt + 1,
                        app_user_id,
                        last_error,
                    )
                    raise RevenueCatAPIError(f"RevenueCat API returned {last_error}")
                logger.warning(
                    "revenuecat_api_error attempt=%d app_user_id=%s error=%s",
                    attempt + 1,
                    app_user_id,
                    last_error,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    continue

            if attempt < self.max_retries:
                await asyncio.sleep(0.5 * (2**attempt))

        raise RevenueCatAPIError(f"RevenueCat API unavailable after {self.max_retries + 1} attempts ({last_error})")

    async def grant_promotional_entitlement(
        self,
        app_user_id: str,
        entitlement_id: str,
        *,
        duration: str = "lifetime",
    ) -> dict[str, Any]:
        """Grants a RevenueCat promotional entitlement using a secret API key.

        RevenueCat treats the grant as an entitlement source alongside store
        transactions, so CustomerInfo, restores and backend verification all
        converge on the same state.
        """
        if not self.is_configured:
            raise RevenueCatAPIError("REVENUECAT_API_KEY is not configured.")

        allowed_durations = {
            "daily",
            "three_day",
            "weekly",
            "two_week",
            "monthly",
            "two_month",
            "three_month",
            "six_month",
            "yearly",
            "lifetime",
        }
        if duration not in allowed_durations:
            raise RevenueCatAPIError("Unsupported promotional entitlement duration.")

        user_id = quote(app_user_id, safe="")
        entitlement = quote(entitlement_id, safe="")
        url = f"{REVENUECAT_API_BASE_URL}/subscribers/{user_id}/entitlements/{entitlement}/promotional"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        last_error = "unknown error"
        for attempt in range(self.max_retries + 1):
            response: httpx.Response | None = None
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json={"duration": duration})
            except httpx.HTTPError as exc:
                last_error = f"network error: {type(exc).__name__}"
            else:
                if response.status_code in {200, 201}:
                    return response.json()
                last_error = f"HTTP {response.status_code}"
                if response.status_code != 429 and response.status_code < 500:
                    raise RevenueCatAPIError(f"RevenueCat API returned {last_error}")

            logger.warning(
                "revenuecat_promo_grant_error attempt=%d app_user_id=%s error=%s",
                attempt + 1,
                app_user_id,
                last_error,
            )
            if attempt < self.max_retries:
                if response is not None:
                    await asyncio.sleep(self._retry_delay(response, attempt))
                else:
                    await asyncio.sleep(0.5 * (2**attempt))

        raise RevenueCatAPIError(
            f"RevenueCat promotional grant failed after {self.max_retries + 1} attempts ({last_error})"
        )

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _RETRY_AFTER_CAP_SECONDS)
            except ValueError:
                pass
        return 0.5 * (2**attempt)
