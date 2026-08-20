import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.admin import AdminAuditLog, UserDeletionJob
from app.models.daily_summary import DailySummary
from app.models.meal import Meal, MealItem
from app.models.promo_code import PromoCode
from app.models.user import User
from app.services.admin_deletion import _delete_database_records, _erase_completed_job_personal_data, initial_steps
from app.services.promo_code_service import promo_code_digest
from app.services.revenuecat_service import RevenueCatClient


def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token_admin_operator"}


@pytest.fixture
async def admin_target(db_session):
    suffix = uuid.uuid4().hex[:8]
    user = User(
        firebase_uid=f"firebase-{suffix}",
        email=f"target-{suffix}@example.com",
        name="Deletion Target",
        onboarding_status="completed",
        revenuecat_app_user_id=f"rc-{suffix}",
    )
    db_session.add(user)
    await db_session.flush()
    meal = Meal(user_id=user.id, source_type="photo", original_input="private meal", image_url="/static/uploads/a.jpg")
    db_session.add(meal)
    await db_session.flush()
    db_session.add(MealItem(meal_id=meal.id, name="Food", weight_grams=100, calories_per_100g=100))
    db_session.add(DailySummary(user_id=user.id, date=__import__("datetime").date.today()))
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_admin_rejects_verified_non_admin(client):
    response = await client.get("/api/v1/admin/me", headers={"Authorization": "Bearer mock_token_regular_user"})
    assert response.status_code == 403
    assert response.json()["error_code"] == "ADMIN_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_admin_creates_hashed_promo_code_and_audits_without_plaintext(client, db_session, monkeypatch):
    pepper = "admin-test-promo-pepper-with-enough-entropy"
    monkeypatch.setattr("app.api.v1.routes.admin.settings.PROMO_CODE_PEPPER", pepper)

    response = await client.post(
        "/api/v1/admin/promo-codes",
        headers=admin_headers(),
        json={"grant_duration": "monthly", "max_redemptions": 5, "valid_days": 30},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"].startswith("CALRY-")
    assert len(body["code"].split("-")) == 5
    assert body["grant_duration"] == "monthly"
    assert body["max_redemptions"] == 5
    assert body["valid_until"] is not None

    promo = await db_session.get(PromoCode, body["id"])
    assert promo is not None
    assert promo.code_digest == promo_code_digest(body["code"], pepper)
    assert body["code"] not in promo.code_hint
    audit = await db_session.scalar(
        select(AdminAuditLog).where(AdminAuditLog.action == "promo_code_created")
    )
    assert audit is not None
    assert audit.metadata_json["promo_code_id"] == promo.id
    assert body["code"] not in str(audit.metadata_json)


@pytest.mark.asyncio
async def test_admin_promo_code_requires_server_pepper(client, monkeypatch):
    monkeypatch.setattr("app.api.v1.routes.admin.settings.PROMO_CODE_PEPPER", None)
    response = await client.post(
        "/api/v1/admin/promo-codes",
        headers=admin_headers(),
        json={"grant_duration": "lifetime", "max_redemptions": 1, "valid_days": None},
    )
    assert response.status_code == 503
    assert response.json()["error_code"] == "PROMO_CODE_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_admin_search_and_preview_are_server_calculated(client, admin_target):
    search = await client.get(f"/api/v1/admin/users/search?q={admin_target.id}", headers=admin_headers())
    assert search.status_code == 200
    result = search.json()["results"][0]
    assert result["id"] == admin_target.id
    assert result["firebase_uid"] != admin_target.firebase_uid

    preview = await client.get(
        f"/api/v1/admin/users/{admin_target.id}/deletion-preview",
        headers=admin_headers(),
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["inventory"]["meals"] == 1
    assert body["inventory"]["ingredients"] == 1
    assert len(body["preview_version"]) == 64
    assert "may continue billing" in body["warnings"][0]
    assert body["target"]["access_status"] == "active"


@pytest.mark.asyncio
async def test_deletion_requires_exact_confirmation_and_fresh_preview(client, admin_target, monkeypatch):
    monkeypatch.setattr("app.api.v1.routes.admin.process_deletion_job", AsyncMock())
    preview_response = await client.get(
        f"/api/v1/admin/users/{admin_target.id}/deletion-preview",
        headers=admin_headers(),
    )
    version = preview_response.json()["preview_version"]
    base = {
        "reason": "admin_requested_deletion",
        "preview_version": version,
        "idempotency_key": uuid.uuid4().hex,
    }
    wrong = await client.post(
        f"/api/v1/admin/users/{admin_target.id}/deletion-jobs",
        headers=admin_headers(),
        json={**base, "confirmation_value": "other@example.com"},
    )
    assert wrong.status_code == 422
    assert wrong.json()["error_code"] == "DELETION_CONFIRMATION_MISMATCH"

    stale = await client.post(
        f"/api/v1/admin/users/{admin_target.id}/deletion-jobs",
        headers=admin_headers(),
        json={**base, "confirmation_value": admin_target.email, "preview_version": "0" * 64},
    )
    assert stale.status_code == 409
    assert stale.json()["error_code"] == "DELETION_PREVIEW_STALE"


@pytest.mark.asyncio
async def test_job_creation_is_idempotent_and_audited(client, db_session, admin_target, monkeypatch):
    runner = AsyncMock()
    monkeypatch.setattr("app.api.v1.routes.admin.process_deletion_job", runner)
    preview = (
        await client.get(
            f"/api/v1/admin/users/{admin_target.id}/deletion-preview",
            headers=admin_headers(),
        )
    ).json()
    payload = {
        "confirmation_value": str(admin_target.id),
        "reason": "admin_requested_deletion",
        "preview_version": preview["preview_version"],
        "idempotency_key": uuid.uuid4().hex,
    }
    first = await client.post(
        f"/api/v1/admin/users/{admin_target.id}/deletion-jobs",
        headers=admin_headers(),
        json=payload,
    )
    second = await client.post(
        f"/api/v1/admin/users/{admin_target.id}/deletion-jobs",
        headers=admin_headers(),
        json=payload,
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert await db_session.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "deletion_job_created"))


@pytest.mark.asyncio
async def test_admin_restricts_and_restores_access_with_pseudonymous_audit(client, db_session, admin_target):
    response = await client.post(
        f"/api/v1/admin/users/{admin_target.id}/access-restriction",
        headers=admin_headers(),
        json={
            "status": "banned",
            "reason": "fraud_prevention",
            "legal_basis": "legitimate_interest",
            "expires_at": None,
            "confirmation_value": admin_target.email,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "banned"
    assert response.json()["firebase_tokens_revoked"] is True
    await db_session.refresh(admin_target)
    assert admin_target.access_status == "banned"

    audit = await db_session.scalar(
        select(AdminAuditLog).where(AdminAuditLog.action == "user_access_restricted")
    )
    assert audit is not None
    assert audit.safe_target_identifier.startswith("target:")
    assert admin_target.email not in audit.safe_target_identifier
    assert audit.admin_email is None

    restored = await client.post(
        f"/api/v1/admin/users/{admin_target.id}/access-restriction/lift",
        headers=admin_headers(),
        json={"reason": "appeal_accepted"},
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"


@pytest.mark.asyncio
async def test_restricted_user_is_blocked_by_every_authenticated_api(client, db_session):
    user = User(
        firebase_uid="restricted_user",
        email="restricted_user@example.com",
        access_status="banned",
        access_restriction_reason="terms_violation",
        access_restriction_legal_basis="contract_enforcement",
    )
    db_session.add(user)
    await db_session.flush()

    response = await client.get(
        "/api/v1/premium/status",
        headers={"Authorization": "Bearer mock_token_restricted_user"},
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "ACCOUNT_ACCESS_RESTRICTED"


@pytest.mark.asyncio
async def test_promotional_revoke_preserves_store_entitlement(client, db_session, admin_target, monkeypatch):
    admin_target.is_premium = True
    admin_target.premium_entitlement = "Calry Pro"
    admin_target.premium_store = "promotional"
    await db_session.flush()

    async def fake_revoke(self, app_user_id: str, entitlement_id: str) -> dict:
        assert app_user_id == admin_target.revenuecat_app_user_id
        assert entitlement_id == "Calry Pro"
        return {
            "subscriber": {
                "entitlements": {
                    "Calry Pro": {
                        "expires_date": "2099-01-01T00:00:00Z",
                        "product_identifier": "calry_monthly",
                    }
                },
                "subscriptions": {"calry_monthly": {"store": "play_store"}},
            }
        }

    monkeypatch.setattr(RevenueCatClient, "revoke_promotional_entitlements", fake_revoke)
    response = await client.post(
        f"/api/v1/admin/users/{admin_target.id}/premium/revoke-promotional",
        headers=admin_headers(),
        json={"confirmation_value": str(admin_target.id), "reason": "promotion_ended"},
    )
    assert response.status_code == 200
    assert response.json()["promotional_grants_revoked"] is True
    assert response.json()["entitlement_active"] is True
    assert response.json()["store"] == "play_store"


@pytest.mark.asyncio
async def test_completed_deletion_erases_retry_identifiers(db_session, admin_target):
    job = UserDeletionJob(
        target_user_id=admin_target.id,
        target_email=admin_target.email,
        target_firebase_uid=admin_target.firebase_uid,
        target_revenuecat_app_user_id=admin_target.revenuecat_app_user_id,
        requested_by_admin_uid="admin_operator",
        requested_by_admin_email="admin@example.com",
        reason="admin_requested_deletion",
        idempotency_key=uuid.uuid4().hex,
        preview_snapshot_json={"private": admin_target.email},
        steps_json=initial_steps(),
    )
    db_session.add(job)
    await db_session.flush()

    await _erase_completed_job_personal_data(db_session, job)

    assert job.target_email == "[erased]"
    assert job.target_firebase_uid == f"erased:{job.id}"
    assert job.target_revenuecat_app_user_id is None
    assert job.requested_by_admin_email is None
    assert job.preview_snapshot_json == {"erased": True}


@pytest.mark.asyncio
async def test_database_step_deletes_indirect_records(db_session, admin_target):
    job = UserDeletionJob(
        target_user_id=admin_target.id,
        target_email=admin_target.email,
        target_firebase_uid=admin_target.firebase_uid,
        target_revenuecat_app_user_id=admin_target.revenuecat_app_user_id,
        requested_by_admin_uid="admin_operator",
        requested_by_admin_email="admin@example.com",
        reason="admin_requested_deletion",
        idempotency_key=uuid.uuid4().hex,
        preview_snapshot_json={},
        steps_json=initial_steps(),
    )
    db_session.add(job)
    await db_session.flush()
    assert await _delete_database_records(db_session, job) is True
    await db_session.flush()
    assert await db_session.get(User, admin_target.id) is None
    assert (await db_session.scalars(select(Meal).where(Meal.user_id == admin_target.id))).all() == []
