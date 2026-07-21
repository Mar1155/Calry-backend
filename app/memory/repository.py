"""Persistence for the AI Memory System."""

import datetime as dt

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import (
    MemoryBelief,
    MemoryBeliefRevision,
    MemoryEvidence,
    MemoryMoment,
    MemoryNarrative,
    MemorySuppression,
)

VISIBLE_STATUSES = ("provisional", "active", "evolving", "disputed")


class MemoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- suppressions -------------------------------------------------------

    async def suppressed_keys(self, user_id: int) -> set[tuple[str, str, str]]:
        result = await self.db.execute(select(MemorySuppression).where(MemorySuppression.user_id == user_id))
        return {(s.domain, s.concept, s.concept_key) for s in result.scalars().all()}

    async def get_suppression(self, user_id: int, domain: str, concept: str, concept_key: str) -> MemorySuppression | None:
        result = await self.db.execute(
            select(MemorySuppression).where(
                MemorySuppression.user_id == user_id,
                MemorySuppression.domain == domain,
                MemorySuppression.concept == concept,
                MemorySuppression.concept_key == concept_key,
            )
        )
        return result.scalar_one_or_none()

    async def create_suppression(self, user_id: int, domain: str, concept: str, concept_key: str, reason: str) -> MemorySuppression:
        existing = await self.get_suppression(user_id, domain, concept, concept_key)
        if existing is not None:
            return existing
        suppression = MemorySuppression(
            user_id=user_id, domain=domain, concept=concept, concept_key=concept_key, reason=reason
        )
        self.db.add(suppression)
        await self.db.flush()
        return suppression

    async def delete_suppression(self, suppression_id: int, user_id: int) -> MemorySuppression | None:
        suppression = await self.db.get(MemorySuppression, suppression_id)
        if suppression is None or suppression.user_id != user_id:
            return None
        await self.db.delete(suppression)
        await self.db.flush()
        return suppression

    # --- beliefs ------------------------------------------------------------

    async def get_belief_by_identity(
        self, user_id: int, domain: str, concept: str, concept_key: str
    ) -> MemoryBelief | None:
        result = await self.db.execute(
            select(MemoryBelief).where(
                MemoryBelief.user_id == user_id,
                MemoryBelief.domain == domain,
                MemoryBelief.concept == concept,
                MemoryBelief.concept_key == concept_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_belief(self, belief_id: int, user_id: int) -> MemoryBelief | None:
        result = await self.db.execute(
            select(MemoryBelief).where(MemoryBelief.id == belief_id, MemoryBelief.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_beliefs(self, user_id: int, *, include_archived: bool = False) -> list[MemoryBelief]:
        statuses = VISIBLE_STATUSES if not include_archived else None
        stmt = select(MemoryBelief).where(MemoryBelief.user_id == user_id)
        if statuses is not None:
            stmt = stmt.where(MemoryBelief.status.in_(statuses))
        stmt = stmt.order_by(MemoryBelief.domain, MemoryBelief.last_reinforced_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_users_with_beliefs(self) -> list[int]:
        result = await self.db.execute(
            select(MemoryBelief.user_id).where(MemoryBelief.status.in_(VISIBLE_STATUSES)).distinct()
        )
        return list(result.scalars().all())

    async def add_belief(self, belief: MemoryBelief) -> MemoryBelief:
        self.db.add(belief)
        await self.db.flush()
        return belief

    # --- evidence -----------------------------------------------------------

    async def replace_evidence(self, belief_id: int, records: list[MemoryEvidence]) -> None:
        await self.db.execute(delete(MemoryEvidence).where(MemoryEvidence.belief_id == belief_id))
        for record in records:
            record.belief_id = belief_id
            self.db.add(record)
        await self.db.flush()

    async def list_evidence(self, belief_id: int) -> list[MemoryEvidence]:
        result = await self.db.execute(
            select(MemoryEvidence).where(MemoryEvidence.belief_id == belief_id).order_by(MemoryEvidence.observed_at)
        )
        return list(result.scalars().all())

    # --- revisions ----------------------------------------------------------

    async def add_revision(self, revision: MemoryBeliefRevision) -> MemoryBeliefRevision:
        self.db.add(revision)
        await self.db.flush()
        return revision

    async def list_revisions(self, belief_id: int) -> list[MemoryBeliefRevision]:
        result = await self.db.execute(
            select(MemoryBeliefRevision)
            .where(MemoryBeliefRevision.belief_id == belief_id)
            .order_by(MemoryBeliefRevision.revision_no)
        )
        return list(result.scalars().all())

    async def next_revision_no(self, belief_id: int) -> int:
        result = await self.db.execute(
            select(func.coalesce(func.max(MemoryBeliefRevision.revision_no), 0)).where(
                MemoryBeliefRevision.belief_id == belief_id
            )
        )
        return int(result.scalar_one()) + 1

    # --- moments ------------------------------------------------------------

    async def moment_beat_exists(self, user_id: int, beat_key: str, belief_id: int | None = None) -> bool:
        stmt = select(MemoryMoment.id).where(MemoryMoment.user_id == user_id, MemoryMoment.beat_key == beat_key)
        if belief_id is not None:
            stmt = stmt.where(or_(MemoryMoment.belief_id == belief_id, MemoryMoment.belief_id.is_(None)))
        result = await self.db.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def add_moment(self, moment: MemoryMoment) -> MemoryMoment:
        self.db.add(moment)
        await self.db.flush()
        return moment

    async def list_moments(
        self, user_id: int, *, limit: int, cursor_date: dt.date | None = None, cursor_id: int | None = None
    ) -> list[MemoryMoment]:
        stmt = select(MemoryMoment).where(MemoryMoment.user_id == user_id, MemoryMoment.hidden_at.is_(None))
        if cursor_date is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    MemoryMoment.occurred_on < cursor_date,
                    and_(MemoryMoment.occurred_on == cursor_date, MemoryMoment.id < cursor_id),
                )
            )
        stmt = stmt.order_by(MemoryMoment.occurred_on.desc(), MemoryMoment.id.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def latest_moment(self, user_id: int) -> MemoryMoment | None:
        result = await self.db.execute(
            select(MemoryMoment)
            .where(MemoryMoment.user_id == user_id, MemoryMoment.hidden_at.is_(None))
            .order_by(MemoryMoment.occurred_on.desc(), MemoryMoment.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_moment(self, moment_id: int, user_id: int) -> MemoryMoment | None:
        result = await self.db.execute(
            select(MemoryMoment).where(MemoryMoment.id == moment_id, MemoryMoment.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def hide_moments_for_belief(self, belief_id: int) -> None:
        result = await self.db.execute(select(MemoryMoment).where(MemoryMoment.belief_id == belief_id))
        now = dt.datetime.now(dt.UTC)
        for moment in result.scalars().all():
            moment.hidden_at = now
        await self.db.flush()

    # --- narratives ---------------------------------------------------------

    async def get_narrative(self, moment_id: int, locale: str, prompt_version: str) -> MemoryNarrative | None:
        result = await self.db.execute(
            select(MemoryNarrative).where(
                MemoryNarrative.moment_id == moment_id,
                MemoryNarrative.locale == locale,
                MemoryNarrative.prompt_version == prompt_version,
            )
        )
        return result.scalar_one_or_none()

    async def add_narrative(self, narrative: MemoryNarrative) -> MemoryNarrative:
        self.db.add(narrative)
        await self.db.flush()
        return narrative
