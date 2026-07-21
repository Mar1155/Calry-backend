import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.admin import AdminAuditLog, UserDeletionJob
from app.models.daily_summary import DailySummary
from app.models.meal import Meal, MealItem
from app.models.user import User
from app.services.admin_deletion import _delete_database_records, initial_steps


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
    assert "may remain active" in body["warnings"][0]


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
