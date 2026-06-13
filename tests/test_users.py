import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_get_current_user_profile_auto_registers(client: AsyncClient) -> None:
    """Verifies that accessing profile endpoint auto-registers new firebase users."""
    headers = {"Authorization": "Bearer mock_token_user_abc_123"}

    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["firebase_uid"] == "user_abc_123"
    assert data["email"] == "user_abc_123@example.com"
    assert data["daily_calorie_goal"] == 2000
    assert data["goal_type"] == "maintain"
    assert "id" in data


@pytest.mark.asyncio
async def test_update_user_profile_and_daily_goals(client: AsyncClient) -> None:
    """Tests updating display name and daily calorie plans via PATCH /me."""
    headers = {"Authorization": "Bearer mock_token_user_abc_123"}

    # 1. Warm up registration
    await client.get("/api/v1/users/me", headers=headers)

    # 2. Submit partial updates
    payload = {
        "name": "Alex CalorieTracker",
        "daily_calorie_goal": 2200,
        "goal_type": "gain",
    }

    response = await client.patch("/api/v1/users/me", json=payload, headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["name"] == "Alex CalorieTracker"
    assert data["daily_calorie_goal"] == 2200
    assert data["goal_type"] == "gain"


@pytest.mark.asyncio
async def test_get_current_user_profile_links_by_email(client: AsyncClient, db_session: AsyncSession) -> None:
    """Verifies that if a user already exists with the same email but different UID, they get linked."""
    from app.models.user import User
    from app.repositories.user import UserRepository

    user_repo = UserRepository(db_session)
    old_user = User(
        firebase_uid="old_uid_123",
        email="custom_user@example.com",
        name="Old User Name",
        daily_calorie_goal=1800,
        goal_type="lose",
    )
    await user_repo.create(old_user)
    await db_session.commit()

    # Request with a token that maps to email "custom_user@example.com" but UID "custom_user"
    headers = {"Authorization": "Bearer mock_token_custom_user"}
    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["firebase_uid"] == "custom_user"
    assert data["email"] == "custom_user@example.com"
    # Goals and history should be preserved
    assert data["daily_calorie_goal"] == 1800
    assert data["goal_type"] == "lose"
