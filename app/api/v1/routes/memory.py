"""AI Memory System endpoints.

Read path is pure database + deterministic templates — never an LLM. Every
moment and belief is returned with an evidence-backed Why payload.
"""

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.dependencies.db import get_db
from app.dependencies.premium import require_premium_user
from app.memory import narrator
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.memory.value_schemas import validate_value
from app.models.memory import MemoryBelief, MemoryEvidence, MemoryMoment
from app.models.user import User
from app.schemas.memory import (
    MemoryActionResponse,
    MemoryBeliefDetailResponse,
    MemoryBeliefResponse,
    MemoryBeliefsResponse,
    MemoryEvidenceEntry,
    MemoryLatestResponse,
    MemoryMomentResponse,
    MemoryRevisionResponse,
    MemoryTimelineChapter,
    MemoryTimelineResponse,
    MemoryWhy,
)

logger = logging.getLogger("app.api.memory")
router = APIRouter()

_MILESTONE_EXPLANATION = {
    "meals_confirmed": "Based on {count} confirmed meals.",
    "days_together": "Based on {count} days since your first meal.",
    "foods_learned": "Based on {count} distinct foods you have confirmed.",
}


def _primary_locale(accept_language: str | None) -> str:
    if not accept_language:
        return "en"
    primary = accept_language.split(",")[0].split("-")[0].split("_")[0].strip().lower()
    return primary if primary in {"en", "it", "es", "zh", "ja", "ar"} else "en"


def _why_from_evidence(belief: MemoryBelief | None, evidence: list[MemoryEvidence]) -> MemoryWhy:
    breakdown: dict[str, int] = {}
    for item in evidence:
        breakdown[item.evidence_type] = breakdown.get(item.evidence_type, 0) + 1
    distinct_days = len({item.observed_at.date() for item in evidence}) if evidence else None
    return MemoryWhy(
        evidence_count=len(evidence),
        span_days=belief.evidence_span_days if belief else 0,
        distinct_days=distinct_days,
        first_seen=min((item.observed_at for item in evidence), default=None),
        last_reinforced=max((item.observed_at for item in evidence), default=None),
        source_breakdown=breakdown,
        evidence=[
            MemoryEvidenceEntry(
                evidence_type=item.evidence_type,
                ref_table=item.ref_table,
                ref_id=item.ref_id,
                observed_at=item.observed_at,
                detail=item.detail_json or {},
            )
            for item in evidence
        ],
    )


def _why_for_moment(moment: MemoryMoment, belief: MemoryBelief | None, evidence: list[MemoryEvidence]) -> MemoryWhy:
    if belief is not None or evidence:
        return _why_from_evidence(belief, evidence)
    # Deterministic milestone: the basis is the counter itself.
    fact = moment.fact_json or {}
    template = _MILESTONE_EXPLANATION.get(fact.get("milestone", ""), "Based on your confirmed meal history.")
    return MemoryWhy(explanation=template.format(count=fact.get("count", 0)))


def _moment_response(moment: MemoryMoment, why: MemoryWhy, locale: str) -> MemoryMomentResponse:
    text = narrator.render_moment(moment.moment_kind, moment.domain, moment.fact_json or {}, locale)
    return MemoryMomentResponse(
        id=moment.id,
        moment_kind=moment.moment_kind,
        domain=moment.domain,
        beat_key=moment.beat_key,
        text=text,
        confidence_at=moment.confidence_at,
        evidence_span_days=moment.evidence_span_days,
        occurred_on=moment.occurred_on,
        chapter_key=moment.chapter_key,
        belief_id=moment.belief_id,
        why=why,
    )


def _belief_response(belief: MemoryBelief, locale: str) -> MemoryBeliefResponse:
    value = belief.value_json or {}
    try:
        validate_value(belief.domain, value)
    except Exception:
        logger.warning("event=memory_belief_invalid_value belief_id=%s domain=%s", belief.id, belief.domain)
    return MemoryBeliefResponse(
        id=belief.id,
        domain=belief.domain,
        concept=belief.concept,
        concept_key=belief.concept_key,
        status=belief.status,
        value=value,
        confidence=belief.confidence,
        statement=narrator.render_belief_statement(belief.domain, value, locale),
        first_seen_at=belief.first_seen_at,
        last_reinforced_at=belief.last_reinforced_at,
        evidence_span_days=belief.evidence_span_days,
        observation_count=belief.observation_count,
        dispute_count=belief.dispute_count,
    )


def _encode_cursor(moment: MemoryMoment) -> str:
    return f"{moment.occurred_on.isoformat()}_{moment.id}"


def _decode_cursor(cursor: str | None) -> tuple[dt.date | None, int | None]:
    if not cursor:
        return None, None
    try:
        date_part, id_part = cursor.rsplit("_", 1)
        return dt.date.fromisoformat(date_part), int(id_part)
    except ValueError:
        return None, None


async def _load_moment_with_why(repo: MemoryRepository, moment: MemoryMoment, locale: str) -> MemoryMomentResponse:
    belief = await repo.get_belief(moment.belief_id, moment.user_id) if moment.belief_id else None
    evidence = await repo.list_evidence(moment.belief_id) if moment.belief_id else []
    return _moment_response(moment, _why_for_moment(moment, belief, evidence), locale)


@router.get("/latest", response_model=MemoryLatestResponse)
async def get_latest_memory(
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: Annotated[str | None, Header()] = None,
) -> MemoryLatestResponse:
    if not settings.MEMORY_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory is disabled.")
    repo = MemoryRepository(db)
    moment = await repo.latest_moment(current_user.id)
    if moment is None:
        return MemoryLatestResponse(moment=None)
    locale = _primary_locale(accept_language)
    return MemoryLatestResponse(moment=await _load_moment_with_why(repo, moment, locale))


@router.get("/timeline", response_model=MemoryTimelineResponse)
async def get_memory_timeline(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=settings.MEMORY_MAX_TIMELINE_LIMIT),
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: Annotated[str | None, Header()] = None,
) -> MemoryTimelineResponse:
    if not settings.MEMORY_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory is disabled.")
    repo = MemoryRepository(db)
    locale = _primary_locale(accept_language)
    cursor_date, cursor_id = _decode_cursor(cursor)
    moments = await repo.list_moments(current_user.id, limit=limit, cursor_date=cursor_date, cursor_id=cursor_id)

    chapters: dict[str, MemoryTimelineChapter] = {}
    for moment in moments:
        response = await _load_moment_with_why(repo, moment, locale)
        chapters.setdefault(moment.chapter_key, MemoryTimelineChapter(chapter_key=moment.chapter_key)).moments.append(response)

    next_cursor = _encode_cursor(moments[-1]) if len(moments) == limit else None
    return MemoryTimelineResponse(chapters=list(chapters.values()), next_cursor=next_cursor)


@router.get("/beliefs", response_model=MemoryBeliefsResponse)
async def list_memory_beliefs(
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: Annotated[str | None, Header()] = None,
) -> MemoryBeliefsResponse:
    if not settings.MEMORY_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory is disabled.")
    repo = MemoryRepository(db)
    locale = _primary_locale(accept_language)
    beliefs = await repo.list_beliefs(current_user.id)
    return MemoryBeliefsResponse(beliefs=[_belief_response(belief, locale) for belief in beliefs])


@router.get("/beliefs/{belief_id}", response_model=MemoryBeliefDetailResponse)
async def get_memory_belief(
    belief_id: int,
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
    accept_language: Annotated[str | None, Header()] = None,
) -> MemoryBeliefDetailResponse:
    if not settings.MEMORY_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory is disabled.")
    repo = MemoryRepository(db)
    belief = await repo.get_belief(belief_id, current_user.id)
    if belief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found.")
    locale = _primary_locale(accept_language)
    evidence = await repo.list_evidence(belief.id)
    revisions = await repo.list_revisions(belief.id)
    return MemoryBeliefDetailResponse(
        belief=_belief_response(belief, locale),
        why=_why_from_evidence(belief, evidence),
        revisions=[
            MemoryRevisionResponse(
                revision_no=revision.revision_no,
                reason=revision.reason,
                from_value=revision.from_value_json or {},
                to_value=revision.to_value_json or {},
                from_confidence=revision.from_confidence,
                to_confidence=revision.to_confidence,
                created_at=revision.created_at,
            )
            for revision in revisions
        ],
    )


@router.post("/beliefs/{belief_id}/not-correct", response_model=MemoryActionResponse)
async def mark_memory_not_correct(
    belief_id: int,
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
) -> MemoryActionResponse:
    """'Not correct': recoverable feedback. Distinct from forgetting."""
    service = MemoryService(db)
    belief = await service.not_correct(belief_id, current_user.id)
    if belief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found.")
    await db.commit()
    return MemoryActionResponse(ok=True, detail="Thanks — I'll relearn this from future meals.")


@router.post("/beliefs/{belief_id}/forget", response_model=MemoryActionResponse)
async def forget_memory(
    belief_id: int,
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
) -> MemoryActionResponse:
    """'Forget this': permanent suppression; blocks re-derivation."""
    service = MemoryService(db)
    suppression = await service.forget(belief_id, current_user.id)
    if suppression is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found.")
    await db.commit()
    return MemoryActionResponse(ok=True, detail="Forgotten. I won't re-derive this memory.")


@router.delete("/suppressions/{suppression_id}", response_model=MemoryActionResponse)
async def unforget_memory(
    suppression_id: int,
    current_user: User = Depends(require_premium_user),
    db: AsyncSession = Depends(get_db),
) -> MemoryActionResponse:
    service = MemoryService(db)
    suppression = await service.unforget(suppression_id, current_user.id)
    if suppression is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suppression not found.")
    await db.commit()
    return MemoryActionResponse(ok=True, detail="Restored. This memory can form again.")
