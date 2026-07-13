import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.promo_code import PromoCode, PromoCodeRedemption
from app.models.user import User
from app.services.promo_code_service import promo_code_digest, promo_code_hint
from app.services.revenuecat_service import RevenueCatClient


def _promotional_subscriber() -> dict:
    return {
        "subscriber": {
            "entitlements": {
                "Calry Pro": {
                    "expires_date": None,
                    "product_identifier": "rc_promo_calry_pro_lifetime",
                }
            },
            "subscriptions": {
                "rc_promo_calry_pro_lifetime": {
                    "store": "promotional",
                }
            },
        }
    }


def _mock_identity(uid: str) -> dict:
    return {"uid": uid, "email": f"{uid}@example.com", "name": "Promo Test"}


@pytest.mark.asyncio
async def test_redeem_free_code_grants_revenuecat_entitlement_once(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = "CALRY-FOUNDERS-2026"
    pepper = "test-only-promo-pepper-with-enough-entropy"
    monkeypatch.setattr(settings, "PROMO_CODE_PEPPER", pepper)
    monkeypatch.setattr(settings, "REVENUECAT_API_KEY", "sk_test")
    monkeypatch.setattr(
        "app.dependencies.auth.verify_firebase_token",
        lambda _token: _mock_identity("promo_founder_uid"),
    )

    calls: list[tuple[str, str, str]] = []

    async def fake_grant(
        self: RevenueCatClient,
        app_user_id: str,
        entitlement_id: str,
        *,
        duration: str = "lifetime",
    ) -> dict:
        calls.append((app_user_id, entitlement_id, duration))
        return _promotional_subscriber()

    monkeypatch.setattr(RevenueCatClient, "grant_promotional_entitlement", fake_grant)

    db_session.add(
        PromoCode(
            code_digest=promo_code_digest(code, pepper),
            code_hint=promo_code_hint(code),
            max_redemptions=10,
        )
    )
    await db_session.commit()

    headers = {"Authorization": "Bearer mock_token_promo_founder"}
    await client.get("/api/v1/users/me", headers=headers)

    response = await client.post("/api/v1/premium/redeem-code", headers=headers, json={"code": code})
    assert response.status_code == 200
    assert response.json()["redeemed"] is True
    assert response.json()["is_premium"] is True
    assert response.json()["source"] == "promo_code"
    assert response.json()["store"] == "promotional"
    assert len(calls) == 1

    second = await client.post("/api/v1/premium/redeem-code", headers=headers, json={"code": code})
    assert second.status_code == 200
    assert second.json()["redeemed"] is False
    assert len(calls) == 1

    promo = (await db_session.execute(select(PromoCode))).scalar_one()
    redemptions = list((await db_session.execute(select(PromoCodeRedemption))).scalars().all())
    user = (await db_session.execute(select(User).where(User.firebase_uid == "promo_founder_uid"))).scalar_one()
    assert promo.redemption_count == 1
    assert len(redemptions) == 1
    assert redemptions[0].status == "granted"
    assert user.is_premium is True


@pytest.mark.asyncio
async def test_invalid_code_does_not_leak_validity(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "PROMO_CODE_PEPPER", "test-only-promo-pepper-with-enough-entropy")
    monkeypatch.setattr(settings, "REVENUECAT_API_KEY", "sk_test")
    monkeypatch.setattr(
        "app.dependencies.auth.verify_firebase_token",
        lambda _token: _mock_identity("promo_invalid_uid"),
    )
    headers = {"Authorization": "Bearer mock_token_promo_invalid"}
    await client.get("/api/v1/users/me", headers=headers)

    response = await client.post(
        "/api/v1/premium/redeem-code",
        headers=headers,
        json={"code": "CALRY-NOT-VALID"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Code is invalid or unavailable."


@pytest.mark.asyncio
async def test_backend_refresh_uses_authenticated_firebase_uid(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def fake_get(self: RevenueCatClient, app_user_id: str) -> dict:
        seen.append(app_user_id)
        return _promotional_subscriber()

    monkeypatch.setattr(settings, "REVENUECAT_API_KEY", "sk_test")
    monkeypatch.setattr(RevenueCatClient, "get_subscriber", fake_get)
    monkeypatch.setattr(
        "app.dependencies.auth.verify_firebase_token",
        lambda _token: _mock_identity("refresh_secure_uid"),
    )
    headers = {"Authorization": "Bearer mock_token_refresh_secure"}

    response = await client.post("/api/v1/premium/refresh", headers=headers)
    assert response.status_code == 200
    assert response.json()["is_premium"] is True
    assert response.json()["source"] == "promo_code"
    assert seen == ["refresh_secure_uid"]
