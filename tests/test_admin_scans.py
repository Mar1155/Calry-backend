"""Admin scan audit (C27): inference logs linked to meals, quality capture,
list/summary/detail/review/export endpoints, and privacy guarantees."""
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult
from app.ai.services.inference_logger import AIInferenceLogger
from app.api.v1.routes.admin_scans import _input_text
from app.models.admin import AdminAuditLog
from app.models.inference import AIInferenceLog
from app.models.meal import Meal, MealItem
from app.models.scan_review import ScanReview
from app.models.user import User
from app.services.admin_deletion import _delete_database_records


def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token_admin_scans"}


def _estimate(**overrides) -> MealEstimateResult:
    data = {
        "meal_name": "Pizza margherita",
        "estimated_calories": 800,
        "estimated_min_calories": 700,
        "estimated_max_calories": 950,
        "confidence": "medium",
        "source_type": "text",
        "items": [
            MealEstimateItem(name="Impasto", weight_grams=200, calories_per_100g=270.0),
            MealEstimateItem(name="Mozzarella", weight_grams=90, calories_per_100g=280.0),
        ],
        "model_name": "test/model",
        "prompt_version": "text_meal_estimation_v9",
    }
    data.update(overrides)
    return MealEstimateResult(**data)


async def _user(db_session) -> User:
    suffix = uuid.uuid4().hex[:8]
    user = User(firebase_uid=f"scan-{suffix}", email=f"scan-{suffix}@example.com", name="Scan User")
    db_session.add(user)
    await db_session.flush()
    return user


async def _scan(db_session, user: User, **overrides) -> tuple[AIInferenceLog, Meal]:
    meal = Meal(
        user_id=user.id,
        source_type=overrides.pop("source_type", "text"),
        original_input="pizza margherita",
        meal_name="Pizza margherita",
        estimated_calories=800,
        confirmed_calories=overrides.pop("confirmed_calories", None),
        correction_percent=overrides.pop("correction_percent", None),
        image_url=overrides.pop("image_url", None),
    )
    db_session.add(meal)
    await db_session.flush()
    db_session.add(MealItem(meal_id=meal.id, name="Impasto", weight_grams=200, calories_per_100g=270))
    log = AIInferenceLog(
        user_id=user.id,
        provider="openrouter",
        model_name="test/model",
        prompt_version="text_meal_estimation_v9",
        input_type=overrides.pop("input_type", "text_stream"),
        raw_input=overrides.pop("raw_input", "pizza margherita"),
        raw_output=json.dumps({"meal_name": "Pizza", "items": [{"name": "Impasto", "weight_grams": 200, "calories_per_100g": 270}]}),
        latency_ms=1200,
        success=True,
        meal_id=meal.id,
        item_count=overrides.pop("item_count", 2),
        estimated_calories=800,
        degraded_extraction=overrides.pop("degraded_extraction", False),
        finish_reason=overrides.pop("finish_reason", "stop"),
        quality_json={"density_clamped": False},
        **overrides,
    )
    db_session.add(log)
    await db_session.flush()
    return log, meal


# ---- capture & linking -------------------------------------------------------


@pytest.mark.asyncio
async def test_log_call_captures_quality_and_links_result(db_session):
    user = await _user(db_session)
    result = _estimate(degraded_extraction=True, density_clamped=True)
    logger = AIInferenceLogger(db_session)
    log_id = await logger.log_call(
        user_id=user.id,
        provider="openrouter",
        model_name="test/model",
        prompt_version="v",
        input_type="text",
        raw_input="pizza",
        raw_output="{}",
        latency_ms=10,
        success=True,
        finish_reason="length",
        result=result,
    )
    assert log_id is not None and result.linked_inference_log_ids == [log_id]
    row = await db_session.get(AIInferenceLog, log_id)
    assert row.item_count == 2
    assert row.degraded_extraction is True
    assert row.finish_reason == "length"
    assert row.quality_json["density_clamped"] is True

    meal = Meal(user_id=user.id, source_type="text", original_input="pizza")
    db_session.add(meal)
    await db_session.flush()
    await logger.link_to_meal([log_id], meal.id)
    await db_session.refresh(row)
    assert row.meal_id == meal.id


@pytest.mark.asyncio
async def test_text_meal_route_links_its_inference_log(client, db_session):
    headers = {"Authorization": "Bearer mock_token_scan_link"}
    await client.get("/api/v1/users/me", headers=headers)
    with patch(
        "app.ai.providers.openrouter.OpenRouterProvider.estimate_meal_from_text", new_callable=AsyncMock
    ) as provider:
        provider.return_value = _estimate()
        response = await client.post("/api/v1/meals/text", json={"text": "pizza margherita"}, headers=headers)
    assert response.status_code == 201
    meal_id = response.json()["id"]
    log = await db_session.scalar(select(AIInferenceLog).where(AIInferenceLog.meal_id == meal_id))
    assert log is not None
    assert log.input_type == "text"
    assert log.item_count == 2


def test_photo_input_text_drops_media_url():
    log = AIInferenceLog(
        input_type="photo_stream",
        raw_input="Image URL: https://bucket.s3.amazonaws.com/uploads/1/a.jpg | Hint: pizza metà e metà",
    )
    assert _input_text(log) == "pizza metà e metà"


# ---- endpoints ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_endpoints_require_admin(client):
    response = await client.get("/api/v1/admin/scans", headers={"Authorization": "Bearer mock_token_regular"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_scan_list_filters_and_flags(client, db_session):
    user = await _user(db_session)
    ok_log, _ = await _scan(db_session, user)
    bad_log, _ = await _scan(db_session, user, item_count=1, degraded_extraction=True, finish_reason="length")

    response = await client.get(f"/api/v1/admin/scans?user_id={user.id}", headers=admin_headers())
    assert response.status_code == 200
    body = response.json()
    ids = {row["id"] for row in body["results"]}
    assert {ok_log.id, bad_log.id} <= ids
    bad = next(row for row in body["results"] if row["id"] == bad_log.id)
    assert {"truncated", "degraded", "single_item"} <= set(bad["flags"])
    assert bad["channel"] == "text"

    truncated = await client.get(
        f"/api/v1/admin/scans?user_id={user.id}&status=truncated", headers=admin_headers()
    )
    assert [row["id"] for row in truncated.json()["results"]] == [bad_log.id]


@pytest.mark.asyncio
async def test_scan_summary_is_aggregate_only(client, db_session):
    user = await _user(db_session)
    await _scan(db_session, user, confirmed_calories=1000, correction_percent=25.0)
    await _scan(db_session, user, finish_reason="length")
    response = await client.get("/api/v1/admin/scans/summary?days=7", headers=admin_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["overall"]["total"] >= 2
    assert body["overall"]["truncated"]["count"] >= 1
    assert any(channel["channel"] == "text" for channel in body["channels"])
    assert len(body["daily"]) == 8
    assert "pizza" not in json.dumps(body).lower()


@pytest.mark.asyncio
async def test_scan_detail_review_and_export(client, db_session):
    user = await _user(db_session)
    log, meal = await _scan(
        db_session,
        user,
        input_type="photo_stream",
        source_type="photo",
        raw_input="Image URL: https://bucket.s3.amazonaws.com/uploads/1/a.jpg | Hint: pizza",
    )

    detail = await client.get(f"/api/v1/admin/scans/{log.id}", headers=admin_headers())
    assert detail.status_code == 200
    body = detail.json()
    assert body["meal"]["id"] == meal.id
    assert body["meal"]["items"][0]["name"] == "Impasto"
    assert body["parsed_output"]["meal_name"] == "Pizza"
    assert "amazonaws" not in body["raw_input"]
    assert body["review"] is None

    review = await client.post(
        f"/api/v1/admin/scans/{log.id}/review",
        json={"verdict": "not_decomposed", "expected_calories": 950, "note": "missing salame"},
        headers=admin_headers(),
    )
    assert review.status_code == 200
    assert review.json()["verdict"] == "not_decomposed"

    flagged = await client.get(f"/api/v1/admin/scans?user_id={user.id}&status=flagged&kind=all", headers=admin_headers())
    assert [row["id"] for row in flagged.json()["results"]] == [log.id]

    export = await client.get("/api/v1/admin/scans/export?days=30", headers=admin_headers())
    item = next(row for row in export.json()["results"] if row["log_id"] == log.id)
    assert item["verdict"] == "not_decomposed"
    assert item["expected_calories"] == 950
    assert item["input_text"] == "pizza"
    assert item["predicted_items"][0]["name"] == "Impasto"

    cleared = await client.post(f"/api/v1/admin/scans/{log.id}/review/clear", headers=admin_headers())
    assert cleared.status_code == 204
    assert await db_session.scalar(select(ScanReview).where(ScanReview.inference_log_id == log.id)) is None

    actions = set(await db_session.scalars(select(AdminAuditLog.action)))
    assert {"scan_viewed", "scan_reviewed", "scan_export", "scan_review_cleared"} <= actions


@pytest.mark.asyncio
async def test_scan_media_is_proxied(client, db_session, tmp_path, monkeypatch):
    from app.services import storage

    monkeypatch.setattr(storage, "UPLOAD_DIR", tmp_path)
    (tmp_path / "scan.jpg").write_bytes(b"\xff\xd8\xff-jpeg")
    user = await _user(db_session)
    log, _ = await _scan(db_session, user, input_type="photo", source_type="photo", image_url="/static/uploads/scan.jpg")

    response = await client.get(f"/api/v1/admin/scans/{log.id}/media?kind=image", headers=admin_headers())
    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xff-jpeg"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"

    missing = await client.get(f"/api/v1/admin/scans/{log.id}/media?kind=audio", headers=admin_headers())
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_non_scan_logs_are_not_exposed(client, db_session):
    user = await _user(db_session)
    other = AIInferenceLog(
        user_id=user.id, provider="openrouter", model_name="m", prompt_version="v",
        input_type="meal_completion", raw_input="x", latency_ms=1, success=True,
    )
    db_session.add(other)
    await db_session.flush()
    response = await client.get(f"/api/v1/admin/scans/{other.id}", headers=admin_headers())
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_user_deletion_removes_scan_reviews(db_session):
    user = await _user(db_session)
    log, _ = await _scan(db_session, user)
    db_session.add(ScanReview(inference_log_id=log.id, user_id=user.id, verdict="correct", reviewed_by_admin_uid="admin"))
    await db_session.flush()

    class Job:
        target_user_id = user.id
        target_revenuecat_app_user_id = None
        target_firebase_uid = user.firebase_uid

    await _delete_database_records(db_session, Job())
    assert await db_session.scalar(select(ScanReview).where(ScanReview.user_id == user.id)) is None
    assert await db_session.scalar(select(AIInferenceLog).where(AIInferenceLog.user_id == user.id)) is None
