import logging
import os
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.core.config import settings

logger = logging.getLogger("app.core.observability")

# Never let credentials or health-adjacent payloads reach Sentry.
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "x-api-key", "x-calry-admin"})


def _release() -> str | None:
    """Release tag: explicit override, else the commit Railway built from."""
    return settings.SENTRY_RELEASE or os.getenv("RAILWAY_GIT_COMMIT_SHA") or None


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("request") or {}
    headers = request.get("headers")
    if isinstance(headers, dict):
        for name in list(headers):
            if name.lower() in _SENSITIVE_HEADERS:
                headers[name] = "[Filtered]"
    # Request bodies carry meals, photos and voice notes; the stack is enough.
    request.pop("data", None)
    return event


def init_sentry(service: str) -> None:
    """Initialise Sentry for one process. ``service`` is ``api`` or ``worker``.

    No-op without ``SENTRY_DSN`` so local runs and tests stay quiet. Safe to
    call once per process; the API calls it at import, the Celery worker on
    ``celeryd_init`` (and per prefork child via ``worker_process_init``).
    """
    if not settings.SENTRY_DSN:
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        release=_release(),
        server_name=service,
        send_default_pii=False,
        enable_logs=True,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profile_session_sample_rate=settings.SENTRY_PROFILE_SESSION_SAMPLE_RATE,
        profile_lifecycle="trace",
        before_send=_before_send,
        integrations=[
            # INFO+ become breadcrumbs, ERROR+ (logger.exception/error) become
            # events, so caught-and-logged failures are tracked too.
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            AsyncioIntegration(),
            CeleryIntegration(monitor_beat_tasks=True, propagate_traces=True),
            SqlalchemyIntegration(),
            HttpxIntegration(),
            RedisIntegration(),
        ],
    )
    sentry_sdk.set_tag("service", service)
    logger.info("Sentry enabled service=%s environment=%s", service, settings.ENVIRONMENT)


def set_sentry_user(user_id: Any, *, request_id: str | None = None) -> None:
    """Attach the internal user id (no email/name) to subsequent events."""
    sentry_sdk.set_user({"id": str(user_id)})
    if request_id:
        sentry_sdk.set_tag("request_id", request_id)


def capture_server_error(exc: BaseException) -> None:
    """Report a handled 5xx. Duplicates of an already-captured exception are dropped by the SDK."""
    sentry_sdk.capture_exception(exc)
