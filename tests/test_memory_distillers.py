import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text_normalization import canonicalize_food_name
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.models.memory import MemoryBelief, MemoryMoment

from memory_utils import NOW, make_food_memory, make_meal, make_user

PASTA_KEY = canonicalize_food_name("pasta")[:160]


async def _beliefs(db: AsyncSession, user_id: int, domain: str) -> list[MemoryBelief]:
    result = await db.execute(
        select(MemoryBelief).where(
            MemoryBelief.user_id == user_id, MemoryBelief.domain == domain, MemoryBelief.status != "archived"
        )
    )
    return list(result.scalars().all())


async def _seed_pasta(db: AsyncSession, user, *, count: int = 5, grams: int = 120) -> None:
    for i in range(count):
        await make_meal(db, user, name="pasta", grams=grams, days_ago=i * 5)


async def test_portion_belief_materializes_with_evidence_and_moment(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="dist_portion")
    await _seed_pasta(db_session, user, count=5, grams=120)

    await MemoryService(db_session).distill_user(user.id, now=NOW)

    beliefs = await _beliefs(db_session, user.id, "portion_model")
    assert len(beliefs) == 1
    belief = beliefs[0]
    assert belief.concept_key == PASTA_KEY
    assert belief.value_json["grams"] == 120
    assert belief.status in ("active", "provisional")
    assert belief.confidence >= 0.5

    evidence = await MemoryRepository(db_session).list_evidence(belief.id)
    assert len(evidence) == 5
    assert all(e.ref_table == "meals" for e in evidence)

    moments = (await db_session.execute(select(MemoryMoment).where(MemoryMoment.user_id == user.id))).scalars().all()
    discovery = [m for m in moments if m.moment_kind == "discovery"]
    assert len(discovery) == 1
    assert discovery[0].fact_json["grams"] == 120


async def test_distillation_is_idempotent(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="dist_idem")
    await _seed_pasta(db_session, user, count=5)

    await MemoryService(db_session).distill_user(user.id, now=NOW)
    await MemoryService(db_session).distill_user(user.id, now=NOW)

    assert len(await _beliefs(db_session, user.id, "portion_model")) == 1
    moments = (await db_session.execute(select(MemoryMoment).where(MemoryMoment.user_id == user.id))).scalars().all()
    assert len([m for m in moments if m.moment_kind == "discovery"]) == 1


async def test_below_gate_produces_no_belief(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="dist_below")
    await _seed_pasta(db_session, user, count=2)  # fewer than min_evidence

    await MemoryService(db_session).distill_user(user.id, now=NOW)

    assert await _beliefs(db_session, user.id, "portion_model") == []


async def test_suppression_blocks_derivation(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="dist_supp")
    await _seed_pasta(db_session, user, count=5)
    repo = MemoryRepository(db_session)
    await repo.create_suppression(user.id, "portion_model", "food_portion", PASTA_KEY, reason="forget")

    await MemoryService(db_session).distill_user(user.id, now=NOW)

    assert await _beliefs(db_session, user.id, "portion_model") == []


async def test_calibration_belief_and_band_moments(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="dist_cal")
    for i in range(8):  # 8 no-edit confirmations across >=4 distinct days, span >=14
        await make_meal(db_session, user, name=f"meal {i}", grams=200, correction_delta=0, days_ago=i * 2)

    await MemoryService(db_session).distill_user(user.id, now=NOW)

    beliefs = await _beliefs(db_session, user.id, "ai_calibration")
    scopes = {b.concept_key for b in beliefs}
    assert "overall" in scopes

    moments = (await db_session.execute(select(MemoryMoment).where(MemoryMoment.user_id == user.id))).scalars().all()
    kinds = {m.moment_kind for m in moments}
    assert "learning" in kinds
    assert "calibration" in kinds


async def test_milestone_moment_at_threshold(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="dist_mile")
    for i in range(20):
        await make_meal(db_session, user, name="snack", grams=50, days_ago=0)

    await MemoryService(db_session).distill_user(user.id, now=NOW)

    moments = (await db_session.execute(select(MemoryMoment).where(MemoryMoment.user_id == user.id))).scalars().all()
    beats = {m.beat_key for m in moments}
    assert "m_meals_20" in beats


async def test_preference_regular_materializes(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="dist_pref")
    for i in range(5):  # 5 distinct days spanning 16 days
        await make_meal(db_session, user, name="overnight oats", grams=300, days_ago=i * 4)

    await MemoryService(db_session).distill_user(user.id, now=NOW)

    beliefs = await _beliefs(db_session, user.id, "preference")
    assert len(beliefs) == 1
    assert beliefs[0].value_json["preference_type"] == "regular"


async def test_preference_favourite_from_food_memory(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="dist_fav")
    for i in range(4):
        await make_meal(db_session, user, name="greek yogurt", grams=150, days_ago=i * 5)
    await make_food_memory(
        db_session, user, name="greek yogurt", canonical_key=canonicalize_food_name("greek yogurt"), is_favorite=True
    )

    await MemoryService(db_session).distill_user(user.id, now=NOW)

    beliefs = await _beliefs(db_session, user.id, "preference")
    assert any(b.value_json["preference_type"] == "favourite" for b in beliefs)
