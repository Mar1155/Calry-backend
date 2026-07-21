import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text_normalization import canonicalize_food_name
from app.memory import lifecycle
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.models.memory import MemoryBelief, MemoryMoment, MemorySuppression

from memory_utils import NOW, make_meal, make_user

PASTA_KEY = canonicalize_food_name("pasta")[:160]


async def _portion_belief(db: AsyncSession, user_id: int) -> MemoryBelief | None:
    result = await db.execute(
        select(MemoryBelief).where(
            MemoryBelief.user_id == user_id, MemoryBelief.domain == "portion_model", MemoryBelief.concept_key == PASTA_KEY
        )
    )
    return result.scalar_one_or_none()


async def _seed_pasta(db: AsyncSession, user, *, count: int = 5) -> None:
    for i in range(count):
        await make_meal(db, user, name="pasta", grams=120, days_ago=i * 5)


def test_status_for_thresholds() -> None:
    assert lifecycle.status_for(0.80, domain="portion_model", span_days=30) == "active"
    assert lifecycle.status_for(0.60, domain="portion_model", span_days=30) == "provisional"
    assert lifecycle.status_for(0.40, domain="portion_model", span_days=30) == "archived"


def test_portion_divergence_tolerance() -> None:
    assert lifecycle.portion_diverged({"grams": 100}, {"grams": 120}) is True  # 20% > 15%
    assert lifecycle.portion_diverged({"grams": 100}, {"grams": 110}) is False  # 10% <= 15%


async def test_decay_archives_unreinforced_belief(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="life_decay")
    await _seed_pasta(db_session, user)
    service = MemoryService(db_session)
    await service.distill_user(user.id, now=NOW)
    belief = await _portion_belief(db_session, user.id)
    assert belief is not None and belief.status in ("active", "provisional")

    await service.consolidate_user(user.id, now=NOW + dt.timedelta(days=400))

    belief = await _portion_belief(db_session, user.id)
    assert belief.status == "archived"
    assert belief.archived_at is not None


async def test_not_correct_is_recoverable_and_needs_fresh_evidence(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="life_dispute")
    await _seed_pasta(db_session, user)
    service = MemoryService(db_session)
    await service.distill_user(user.id, now=NOW)
    belief = await _portion_belief(db_session, user.id)
    assert belief is not None

    await service.not_correct(belief.id, user.id, now=NOW)
    belief = await _portion_belief(db_session, user.id)
    assert belief.status == "disputed"
    assert belief.dispute_count == 1
    revisions = await MemoryRepository(db_session).list_revisions(belief.id)
    assert any(r.reason == "dispute" for r in revisions)

    # Same (pre-dispute) evidence does not re-materialize the belief.
    await service.distill_user(user.id, now=NOW)
    belief = await _portion_belief(db_session, user.id)
    assert belief.status == "disputed"

    # Fresh post-dispute evidence rebuilds it, keeping the dispute on record.
    for i in range(5):
        await make_meal(db_session, user, name="pasta", grams=120, now=NOW + dt.timedelta(days=30), days_ago=i * 5)
    await service.distill_user(user.id, now=NOW + dt.timedelta(days=30))
    belief = await _portion_belief(db_session, user.id)
    assert belief.status != "disputed"
    assert belief.dispute_count == 1


async def test_forget_suppresses_and_blocks_rederivation(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="life_forget")
    await _seed_pasta(db_session, user)
    service = MemoryService(db_session)
    repo = MemoryRepository(db_session)
    await service.distill_user(user.id, now=NOW)
    belief = await _portion_belief(db_session, user.id)
    assert belief is not None
    assert await repo.latest_moment(user.id) is not None

    suppression = await service.forget(belief.id, user.id)
    assert suppression is not None
    belief = await _portion_belief(db_session, user.id)
    assert belief.status == "archived"
    assert await repo.latest_moment(user.id) is None  # moments hidden

    # Re-distilling does not bring it back while suppressed.
    await service.distill_user(user.id, now=NOW)
    belief = await _portion_belief(db_session, user.id)
    assert belief.status == "archived"

    # Un-forgetting lifts the suppression so it can form again.
    await service.unforget(suppression.id, user.id)
    remaining = (
        await db_session.execute(select(MemorySuppression).where(MemorySuppression.user_id == user.id))
    ).scalars().all()
    assert remaining == []
    await service.distill_user(user.id, now=NOW)
    belief = await _portion_belief(db_session, user.id)
    assert belief.status in ("active", "provisional")


async def test_value_change_writes_revision_and_evolution_moment(db_session: AsyncSession) -> None:
    user = await make_user(db_session, uid="life_evo")
    for i in range(5):  # established at ~120 g
        await make_meal(db_session, user, name="pasta", grams=120, days_ago=20 + i * 5)
    service = MemoryService(db_session)
    await service.distill_user(user.id, now=NOW)
    belief = await _portion_belief(db_session, user.id)
    assert belief is not None and belief.value_json["grams"] == 120

    # Portions shift materially larger and persist.
    for i in range(6):
        await make_meal(db_session, user, name="pasta", grams=160, days_ago=i * 3)
    await service.distill_user(user.id, now=NOW)

    belief = await _portion_belief(db_session, user.id)
    assert belief.value_json["grams"] > 120
    revisions = await MemoryRepository(db_session).list_revisions(belief.id)
    assert any(r.reason == "value_change" for r in revisions)
    moments = (
        await db_session.execute(select(MemoryMoment).where(MemoryMoment.user_id == user.id, MemoryMoment.moment_kind == "evolution"))
    ).scalars().all()
    assert len(moments) >= 1
