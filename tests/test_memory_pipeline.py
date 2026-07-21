from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.meals import _build_user_context
from app.memory.service import MemoryQueryService

from memory_utils import NOW, make_meal, make_user


async def _user_with_portion(db: AsyncSession, *, suffix: str):
    user = await make_user(db, uid=suffix)
    for i in range(5):
        await make_meal(db, user, name="pasta", grams=120, days_ago=i * 5)
    from app.memory.service import MemoryService

    await MemoryService(db).distill_user(user.id, now=NOW)
    return user


async def test_estimation_hints_match_meal_name(db_session: AsyncSession) -> None:
    user = await _user_with_portion(db_session, suffix="pipe_match")

    hints = await MemoryQueryService(db_session).get_estimation_hints(user.id, meal_name="pasta")

    assert any(h.display_name.lower() == "pasta" and h.grams == 120 for h in hints.portion_hints)
    assert hints.summary is not None
    assert "portion" in hints.summary.lower()


async def test_estimation_hints_offer_general_priors_without_meal_name(db_session: AsyncSession) -> None:
    user = await _user_with_portion(db_session, suffix="pipe_general")

    hints = await MemoryQueryService(db_session).get_estimation_hints(user.id)

    assert hints.portion_hints
    assert hints.summary is not None


async def test_no_memories_yields_no_summary(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="pipe_empty")

    hints = await MemoryQueryService(db_session).get_estimation_hints(user.id, meal_name="pasta")

    assert hints.portion_hints == []
    assert hints.summary is None


async def test_build_user_context_includes_memory_summary(db_session: AsyncSession) -> None:
    user = await _user_with_portion(db_session, suffix="pipe_context")

    context = await _build_user_context(db_session, user, "en")

    assert context.memory_summary is not None
    assert "pasta" in context.memory_summary.lower()
