import datetime as dt
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.revenuecat_event import RevenueCatEvent, RevenueCatSubscriberSnapshot
from app.models.user import User
from app.services.revenuecat_service import RevenueCatAPIError, RevenueCatClient

WEBHOOK_URL = "/api/v1/webhooks/revenuecat"
WEBHOOK_SECRET = "test-webhook-secret"
AUTH_HEADERS = {"Authorization": f"Bearer {WEBHOOK_SECRET}"}


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)


def _future_ms(days: int = 30) -> int:
    return int((dt.datetime.now(dt.UTC) + dt.timedelta(days=days)).timestamp() * 1000)


def _iso(days_from_now: int) -> str:
    return (dt.datetime.now(dt.UTC) + dt.timedelta(days=days_from_now)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _webhook_payload(
    event_type: str = "INITIAL_PURCHASE",
    app_user_id: str | None = "webhook-test-uid",
    event_id: str | None = None,
    entitlement_ids: list[str] | None = None,
    expiration_at_ms: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": event_id or str(uuid.uuid4()),
        "type": event_type,
        "app_user_id": app_user_id,
        "product_id": "calry_premium_monthly",
        "entitlement_ids": ["premium"] if entitlement_ids is None else entitlement_ids,
        "transaction_id": "tx-1000",
        "original_transaction_id": "tx-0999",
        "expiration_at_ms": expiration_at_ms,
        "event_timestamp_ms": _future_ms(0),
        "environment": "SANDBOX",
        "store": "APP_STORE",
        **extra,
    }
    return {"api_version": "1.0", "event": event}


def _subscriber_payload(expires_date: str | None, product: str = "calry_premium_monthly") -> dict[str, Any]:
    return {
        "request_date": _iso(0),
        "subscriber": {
            "entitlements": {
                "premium": {
                    "expires_date": expires_date,
                    "product_identifier": product,
                    "purchase_date": _iso(-1),
                }
            },
            "subscriptions": {product: {"store": "app_store", "expires_date": expires_date}},
        },
    }


async def _create_user(db_session: AsyncSession, uid: str) -> User:
    user = User(firebase_uid=uid, email=f"{uid}@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _mock_rc_api(monkeypatch: pytest.MonkeyPatch, subscriber: dict[str, Any] | Exception) -> list[str]:
    """Configures the API key and mocks get_subscriber. Returns the call log."""
    calls: list[str] = []

    async def fake_get_subscriber(self: RevenueCatClient, app_user_id: str) -> dict[str, Any]:
        calls.append(app_user_id)
        if isinstance(subscriber, Exception):
            raise subscriber
        return subscriber

    monkeypatch.setattr(settings, "REVENUECAT_API_KEY", "sk_test_dummy")
    monkeypatch.setattr(RevenueCatClient, "get_subscriber", fake_get_subscriber)
    return calls


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_secret(client: AsyncClient) -> None:
    response = await client.post(
        WEBHOOK_URL,
        json=_webhook_payload(),
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert response.status_code == 401

    response_missing = await client.post(WEBHOOK_URL, json=_webhook_payload())
    assert response_missing.status_code == 401


@pytest.mark.asyncio
async def test_webhook_requires_secret_in_production(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REVENUECAT_WEBHOOK_SECRET", None)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    response = await client.post(WEBHOOK_URL, json=_webhook_payload())
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_initial_purchase_sets_premium_from_api(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = "rc-initial-purchase-uid"
    user = await _create_user(db_session, uid)
    calls = _mock_rc_api(monkeypatch, _subscriber_payload(expires_date=_iso(30)))

    payload = _webhook_payload(app_user_id=uid, expiration_at_ms=_future_ms())
    response = await client.post(WEBHOOK_URL, json=payload, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "success", "event_processed": "INITIAL_PURCHASE"}
    assert calls == [uid]

    await db_session.refresh(user)
    assert user.is_premium is True
    assert user.premium_entitlement == "premium"
    assert user.premium_expires_at is not None
    assert user.revenuecat_app_user_id == uid
    assert user.premium_store == "app_store"
    assert user.premium_product_id == "calry_premium_monthly"

    event_row = (
        await db_session.execute(
            select(RevenueCatEvent).where(RevenueCatEvent.event_id == payload["event"]["id"])
        )
    ).scalar_one()
    assert event_row.processing_status == "processed"
    assert event_row.payload == payload

    snapshot = (
        await db_session.execute(
            select(RevenueCatSubscriberSnapshot).where(RevenueCatSubscriberSnapshot.app_user_id == uid)
        )
    ).scalar_one()
    assert snapshot.entitlement_active is True
    assert snapshot.user_id == user.id


@pytest.mark.asyncio
async def test_expiration_clears_premium(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = "rc-expiration-uid"
    user = await _create_user(db_session, uid)
    user.is_premium = True
    user.premium_entitlement = "premium"
    await db_session.commit()

    _mock_rc_api(monkeypatch, _subscriber_payload(expires_date=_iso(-1)))
    response = await client.post(
        WEBHOOK_URL,
        json=_webhook_payload(event_type="EXPIRATION", app_user_id=uid),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    await db_session.refresh(user)
    assert user.is_premium is False
    assert user.premium_entitlement is None


@pytest.mark.asyncio
async def test_cancellation_keeps_premium_until_period_end(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CANCELLATION = auto-renew off, but the paid period still runs: stay premium."""
    uid = "rc-cancellation-uid"
    user = await _create_user(db_session, uid)
    user.is_premium = True
    user.premium_entitlement = "premium"
    await db_session.commit()

    # RevenueCat still reports an active entitlement (expires in the future).
    _mock_rc_api(monkeypatch, _subscriber_payload(expires_date=_iso(10)))
    response = await client.post(
        WEBHOOK_URL,
        json=_webhook_payload(event_type="CANCELLATION", app_user_id=uid, expiration_at_ms=_future_ms(10)),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    await db_session.refresh(user)
    assert user.is_premium is True
    assert user.premium_expires_at is not None


@pytest.mark.asyncio
async def test_duplicate_event_is_processed_once(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = "rc-duplicate-uid"
    await _create_user(db_session, uid)
    calls = _mock_rc_api(monkeypatch, _subscriber_payload(expires_date=_iso(30)))

    payload = _webhook_payload(app_user_id=uid, event_id="evt-duplicate-1")
    first = await client.post(WEBHOOK_URL, json=payload, headers=AUTH_HEADERS)
    second = await client.post(WEBHOOK_URL, json=payload, headers=AUTH_HEADERS)

    assert first.status_code == 200
    assert first.json()["status"] == "success"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    # The subscriber was fetched (and the user updated) only once.
    assert calls == [uid]

    event_rows = (
        await db_session.execute(
            select(RevenueCatEvent).where(RevenueCatEvent.event_id == "evt-duplicate-1")
        )
    ).scalars().all()
    assert len(event_rows) == 1


@pytest.mark.asyncio
async def test_api_failure_falls_back_to_payload(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Temporary RevenueCat API outage: state derives from the payload, event still processed."""
    uid = "rc-fallback-uid"
    user = await _create_user(db_session, uid)
    _mock_rc_api(monkeypatch, RevenueCatAPIError("simulated outage"))

    response = await client.post(
        WEBHOOK_URL,
        json=_webhook_payload(event_type="RENEWAL", app_user_id=uid, expiration_at_ms=_future_ms(30)),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    await db_session.refresh(user)
    assert user.is_premium is True


@pytest.mark.asyncio
async def test_payload_fallback_expiration_clears_premium(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No API key configured: EXPIRATION clears premium from payload alone."""
    monkeypatch.setattr(settings, "REVENUECAT_API_KEY", None)
    uid = "rc-payload-expiration-uid"
    user = await _create_user(db_session, uid)
    user.is_premium = True
    user.premium_entitlement = "premium"
    await db_session.commit()

    response = await client.post(
        WEBHOOK_URL,
        json=_webhook_payload(
            event_type="EXPIRATION",
            app_user_id=uid,
            expiration_at_ms=_future_ms(-1),
        ),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    await db_session.refresh(user)
    assert user.is_premium is False


@pytest.mark.asyncio
async def test_unknown_user_is_ignored(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_rc_api(monkeypatch, _subscriber_payload(expires_date=_iso(30)))
    payload = _webhook_payload(app_user_id="rc-no-such-user-uid", event_id="evt-no-user-1")

    response = await client.post(WEBHOOK_URL, json=payload, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "User not found"}

    event_row = (
        await db_session.execute(
            select(RevenueCatEvent).where(RevenueCatEvent.event_id == "evt-no-user-1")
        )
    ).scalar_one()
    assert event_row.processing_status == "user_not_found"


@pytest.mark.asyncio
async def test_event_without_app_user_id_is_ignored(client: AsyncClient) -> None:
    payload = _webhook_payload(app_user_id=None, event_id="evt-no-app-user-1")
    response = await client.post(WEBHOOK_URL, json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "No app_user_id"}


@pytest.mark.asyncio
async def test_malformed_payload_is_ignored(client: AsyncClient) -> None:
    response = await client.post(WEBHOOK_URL, json={"not_event": True}, headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "No event object found"}
