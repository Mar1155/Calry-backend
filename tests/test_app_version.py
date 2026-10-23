import pytest
from sqlalchemy import delete

from app.core.app_version import compare_versions, is_valid_version, normalize_platform, parse_version
from app.models.app_setting import AppSetting
from app.services.app_version_service import invalidate_policy_cache


@pytest.fixture(autouse=True)
async def _clean_app_settings(db_session):
    # The admin endpoint commits, so the singleton row must not leak between tests.
    await db_session.execute(delete(AppSetting))
    await db_session.commit()
    yield
    await db_session.rollback()
    await db_session.execute(delete(AppSetting))
    await db_session.commit()


def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token_admin_operator"}


def test_version_compare_ignores_build_and_prerelease() -> None:
    assert compare_versions("1.0.1+42", "1.0.1") == 0
    assert compare_versions("1.2", "1.2.0") == 0
    assert compare_versions("1.10.0", "1.9.9") == 1
    assert compare_versions("0.9.9", "1.0.0") == -1
    assert parse_version("2.0.0-beta+7") == (2, 0, 0)


def test_version_validation_rejects_garbage() -> None:
    for bad in ("abc", "", "1..2", "1.2.x", "v1.0", None):
        assert not is_valid_version(bad)
    with pytest.raises(ValueError):
        parse_version("abc")
    assert normalize_platform("iOS") == "ios"
    assert normalize_platform("windows") is None


async def _set_policy(db, **fields) -> None:
    db.add(AppSetting(id=1, **fields))
    await db.flush()
    invalidate_policy_cache()


@pytest.mark.asyncio
async def test_app_version_returns_platform_override_and_store_url(client, db_session) -> None:
    await _set_policy(
        db_session,
        min_supported_app_version="1.0.0",
        min_supported_ios_version="1.5.0",
        ios_store_url="https://apps.apple.com/app/id1",
    )
    ios = (await client.get("/api/v1/app-version?platform=ios")).json()
    android = (await client.get("/api/v1/app-version", headers={"X-App-Platform": "android"})).json()
    assert ios == {"min_supported_app_version": "1.5.0", "store_url": "https://apps.apple.com/app/id1"}
    assert android == {"min_supported_app_version": "1.0.0", "store_url": None}


@pytest.mark.asyncio
async def test_old_client_gets_426_on_api_routes(client, db_session) -> None:
    await _set_policy(
        db_session,
        min_supported_app_version="1.0.0",
        min_supported_ios_version="2.0.0",
        ios_store_url="https://apps.apple.com/app/id1",
    )
    old = await client.get("/api/v1/users/me", headers={"X-App-Version": "1.9.9+5", "X-App-Platform": "ios"})
    assert old.status_code == 426
    body = old.json()
    assert body["error_code"] == "UPGRADE_REQUIRED"
    assert body["details"] == {"min_supported_app_version": "2.0.0", "store_url": "https://apps.apple.com/app/id1"}

    current = await client.get("/api/v1/users/me", headers={"X-App-Version": "2.0.0", "X-App-Platform": "ios"})
    assert current.status_code != 426
    other_platform = await client.get("/api/v1/users/me", headers={"X-App-Version": "1.0.0", "X-App-Platform": "android"})
    assert other_platform.status_code != 426


@pytest.mark.asyncio
async def test_requests_without_version_headers_and_exempt_routes_are_not_gated(client, db_session) -> None:
    await _set_policy(db_session, min_supported_app_version="99.0.0")
    assert (await client.get("/api/v1/users/me")).status_code != 426
    assert (await client.get("/api/v1/health", headers={"X-App-Version": "1.0.0", "X-App-Platform": "ios"})).status_code == 200
    assert (await client.get("/api/v1/app-version", headers={"X-App-Version": "1.0.0", "X-App-Platform": "ios"})).status_code == 200


@pytest.mark.asyncio
async def test_malformed_stored_threshold_does_not_block_or_crash(client, db_session) -> None:
    await _set_policy(db_session, min_supported_app_version="garbage")
    response = await client.get("/api/v1/app-version")
    assert response.json()["min_supported_app_version"] == "0.0.1"


@pytest.mark.asyncio
async def test_admin_can_read_and_update_policy(client) -> None:
    update = await client.put(
        "/api/v1/admin/app-version",
        headers=admin_headers(),
        json={
            "min_supported_app_version": "1.2.0",
            "min_supported_ios_version": "1.3.0",
            "min_supported_android_version": "",
            "ios_store_url": "https://apps.apple.com/app/id1",
            "android_store_url": None,
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["min_supported_android_version"] is None
    assert (await client.get("/api/v1/admin/app-version", headers=admin_headers())).json()["min_supported_ios_version"] == "1.3.0"
    # cache is invalidated on write
    assert (await client.get("/api/v1/app-version?platform=ios")).json()["min_supported_app_version"] == "1.3.0"


@pytest.mark.asyncio
async def test_admin_update_rejects_malformed_values_and_non_admin(client) -> None:
    for bad in (
        {"min_supported_app_version": "abc"},
        {"min_supported_app_version": "1.0.0", "min_supported_ios_version": "x.y"},
        {"min_supported_app_version": "1.0.0", "ios_store_url": "http://insecure.example"},
    ):
        response = await client.put("/api/v1/admin/app-version", headers=admin_headers(), json=bad)
        assert response.status_code == 422
    denied = await client.put(
        "/api/v1/admin/app-version",
        headers={"Authorization": "Bearer mock_token_regular_user"},
        json={"min_supported_app_version": "1.0.0"},
    )
    assert denied.status_code == 403
