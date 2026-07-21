import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.memory.service import MemoryService
from app.models.memory import MemoryBelief
from app.models.user import User
from app.services.revenuecat_service import RevenueCatAPIError

from memory_utils import NOW, make_meal

pytestmark = pytest.mark.asyncio


async def _premium_user_with_memories(client: AsyncClient, db_session: AsyncSession, monkeypatch, *, suffix: str):
    monkeypatch.setattr(settings, "PREMIUM_BYPASS", True)
    headers = {"Authorization": f"Bearer mock_token_{suffix}"}
    await client.get("/api/v1/users/me", headers=headers)
    user = (await db_session.execute(select(User).where(User.firebase_uid == suffix))).scalar_one()
    for i in range(5):
        await make_meal(db_session, user, name="pasta", grams=120, days_ago=i * 5)
    await MemoryService(db_session).distill_user(user.id, now=NOW)
    await db_session.flush()
    return user, headers


async def test_timeline_returns_chapters_with_why(client: AsyncClient, db_session: AsyncSession, monkeypatch) -> None:
    user, headers = await _premium_user_with_memories(client, db_session, monkeypatch, suffix="mem_api_timeline")

    response = await client.get("/api/v1/memory/timeline", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["chapters"]) >= 1
    moment = data["chapters"][0]["moments"][0]
    assert moment["text"]
    assert moment["why"]["evidence_count"] >= 1
    assert moment["why"]["evidence"]


async def test_beliefs_list_and_detail_with_why(client: AsyncClient, db_session: AsyncSession, monkeypatch) -> None:
    user, headers = await _premium_user_with_memories(client, db_session, monkeypatch, suffix="mem_api_beliefs")

    listed = await client.get("/api/v1/memory/beliefs", headers=headers)
    assert listed.status_code == 200
    beliefs = listed.json()["beliefs"]
    portion = next(b for b in beliefs if b["domain"] == "portion_model")
    assert portion["statement"]
    assert portion["confidence"] >= 0.5

    detail = await client.get(f"/api/v1/memory/beliefs/{portion['id']}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["why"]["evidence_count"] == 5
    assert body["why"]["source_breakdown"]


async def test_latest_returns_most_recent_moment(client: AsyncClient, db_session: AsyncSession, monkeypatch) -> None:
    user, headers = await _premium_user_with_memories(client, db_session, monkeypatch, suffix="mem_api_latest")

    response = await client.get("/api/v1/memory/latest", headers=headers)
    assert response.status_code == 200
    assert response.json()["moment"] is not None


async def test_not_correct_increments_dispute(client: AsyncClient, db_session: AsyncSession, monkeypatch) -> None:
    user, headers = await _premium_user_with_memories(client, db_session, monkeypatch, suffix="mem_api_dispute")
    belief = (
        await db_session.execute(select(MemoryBelief).where(MemoryBelief.user_id == user.id, MemoryBelief.domain == "portion_model"))
    ).scalars().first()

    response = await client.post(f"/api/v1/memory/beliefs/{belief.id}/not-correct", headers=headers)
    assert response.status_code == 200
    await db_session.refresh(belief)
    assert belief.dispute_count == 1
    assert belief.status == "disputed"


async def test_forget_archives_and_hides(client: AsyncClient, db_session: AsyncSession, monkeypatch) -> None:
    user, headers = await _premium_user_with_memories(client, db_session, monkeypatch, suffix="mem_api_forget")
    belief = (
        await db_session.execute(select(MemoryBelief).where(MemoryBelief.user_id == user.id, MemoryBelief.domain == "portion_model"))
    ).scalars().first()

    response = await client.post(f"/api/v1/memory/beliefs/{belief.id}/forget", headers=headers)
    assert response.status_code == 200

    latest = await client.get("/api/v1/memory/latest", headers=headers)
    assert latest.json()["moment"] is None


async def test_free_user_is_forbidden(client: AsyncClient, db_session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setattr(settings, "PREMIUM_BYPASS", False)

    async def _no_refresh(self, user):
        raise RevenueCatAPIError("no key")

    monkeypatch.setattr("app.services.premium_service.PremiumService.refresh_from_revenuecat", _no_refresh)

    headers = {"Authorization": "Bearer mock_token_mem_api_free"}
    await client.get("/api/v1/users/me", headers=headers)

    response = await client.get("/api/v1/memory/timeline", headers=headers)
    assert response.status_code == 403
