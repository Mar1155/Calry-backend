"""Orchestration for the AI Memory System.

``MemoryService`` derives beliefs/moments from confirmed meals (distill), keeps
them fresh (consolidate), and handles user feedback (not-correct vs forget).
``MemoryQueryService`` exposes learned memories to the meal-estimation pipeline.

All logic is deterministic. No LLM is invoked anywhere on these paths.
"""

import datetime as dt
from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.text_normalization import canonicalize_food_name
from app.insights.versioning import InsightVersionService
from app.memory import lifecycle, narrator
from app.memory.confidence import (
    SOURCE_WEIGHTS,
    EvidenceItem,
    confidence_from_evidence,
    passes_gate,
    summarize_evidence,
)
from app.memory.distillers import DistillationContext, MemoryDistiller
from app.memory.repository import MemoryRepository
from app.memory.types import BeliefCandidate, MomentSpec
from app.memory.value_schemas import dump_value
from app.models.memory import (
    MemoryBelief,
    MemoryBeliefRevision,
    MemoryEvidence,
    MemoryMoment,
    MemoryNarrative,
)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _to_items(records) -> list[EvidenceItem]:
    return [EvidenceItem(r.evidence_type, r.observed_at, r.observed_at.date()) for r in records]


class MemoryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = MemoryRepository(db)

    # --- derivation ---------------------------------------------------------

    async def distill_user(self, user_id: int, *, now: dt.datetime | None = None) -> dict:
        """Run every distiller and reconcile beliefs/moments. Idempotent."""
        if not settings.MEMORY_ENABLED:
            return {"skipped": True}
        now = now or _utcnow()
        source_versions = await InsightVersionService(self.db).current(user_id)
        suppressed = await self.repo.suppressed_keys(user_id)
        ctx = DistillationContext(
            db=self.db, user_id=user_id, now=now, source_versions=source_versions, suppressed_keys=suppressed
        )

        candidates: list[BeliefCandidate] = []
        standalone: list[MomentSpec] = []
        for distiller_cls in MemoryDistiller.registry:
            result = await distiller_cls().distill(ctx)
            candidates.extend(result.belief_candidates)
            standalone.extend(result.standalone_moments)

        beliefs_upserted = 0
        moments_emitted = 0
        for candidate in candidates:
            upserted, emitted = await self._process_candidate(ctx, candidate)
            beliefs_upserted += 1 if upserted else 0
            moments_emitted += emitted

        for spec in standalone:
            if await self._emit_moment(user_id, None, spec, "milestone:1.0"):
                moments_emitted += 1

        await self.db.flush()
        return {"beliefs_upserted": beliefs_upserted, "moments_emitted": moments_emitted}

    async def _process_candidate(self, ctx: DistillationContext, candidate: BeliefCandidate) -> tuple[bool, int]:
        user_id = ctx.user_id
        existing = await self.repo.get_belief_by_identity(
            user_id, candidate.domain, candidate.concept, candidate.concept_key
        )

        # A disputed belief re-materializes only on evidence observed after the dispute.
        # The same filtered set drives confidence, the gate, and the stored evidence so
        # distillation and later consolidation stay consistent.
        eval_records = candidate.evidence
        if existing is not None and existing.status == lifecycle.STATUS_DISPUTED and existing.disputed_at is not None:
            eval_records = [r for r in candidate.evidence if r.observed_at > existing.disputed_at]

        eval_items = _to_items(eval_records)
        confidence = confidence_from_evidence(
            eval_items, candidate.domain, consistency=candidate.consistency, now=ctx.now
        )
        stats = summarize_evidence(eval_items)
        gate_ok = passes_gate(eval_items, candidate.domain, confidence=confidence)

        if not gate_ok:
            if existing is not None and existing.status in (
                lifecycle.STATUS_ACTIVE,
                lifecycle.STATUS_PROVISIONAL,
                lifecycle.STATUS_EVOLVING,
            ):
                await self._apply_status(existing, status_for=confidence, domain=candidate.domain, stats=stats, now=ctx.now)
            return False, 0

        value_dict = dump_value(candidate.value)
        emitted = 0
        if existing is None:
            belief = await self._create_belief(ctx, candidate, value_dict, confidence, stats, eval_records)
            if candidate.formation_moment is not None:
                spec = replace(candidate.formation_moment, confidence_at=confidence)
                if await self._emit_moment(user_id, belief.id, spec, f"{candidate.distiller_id}:{candidate.distiller_version}"):
                    emitted += 1
        else:
            emitted += await self._update_belief(ctx, existing, candidate, value_dict, confidence, stats, eval_records)
            belief = existing

        for spec in candidate.extra_moments:
            spec = replace(spec, confidence_at=confidence)
            if await self._emit_moment(user_id, belief.id, spec, f"{candidate.distiller_id}:{candidate.distiller_version}"):
                emitted += 1
        return True, emitted

    async def _create_belief(
        self, ctx: DistillationContext, candidate: BeliefCandidate, value_dict: dict, confidence: float, stats, evidence_records
    ) -> MemoryBelief:
        observed = [r.observed_at for r in evidence_records]
        belief = MemoryBelief(
            user_id=ctx.user_id,
            domain=candidate.domain,
            concept=candidate.concept,
            concept_key=candidate.concept_key,
            status=lifecycle.status_for(confidence, domain=candidate.domain, span_days=stats.span_days),
            value_json=value_dict,
            value_schema_version=candidate.value.schema_version,
            confidence=confidence,
            consistency=candidate.consistency,
            first_seen_at=min(observed) if observed else ctx.now,
            last_reinforced_at=max(observed) if observed else ctx.now,
            evidence_span_days=stats.span_days,
            observation_count=stats.count,
            distiller_id=candidate.distiller_id,
            distiller_version=candidate.distiller_version,
            source_versions_json=candidate.source_versions,
        )
        await self.repo.add_belief(belief)
        await self._store_evidence(belief.id, evidence_records)
        return belief

    async def _update_belief(
        self,
        ctx: DistillationContext,
        belief: MemoryBelief,
        candidate: BeliefCandidate,
        value_dict: dict,
        confidence: float,
        stats,
        evidence_records,
    ) -> int:
        old_value = belief.value_json or {}
        changed = (belief.domain == "portion_model" and lifecycle.portion_diverged(old_value, value_dict)) or (
            belief.domain == "preference" and lifecycle.preference_changed(old_value, value_dict)
        )
        emitted = 0
        if changed:
            revision_no = await self.repo.next_revision_no(belief.id)
            await self.repo.add_revision(
                MemoryBeliefRevision(
                    belief_id=belief.id,
                    revision_no=revision_no,
                    from_value_json=old_value,
                    to_value_json=value_dict,
                    from_confidence=belief.confidence,
                    to_confidence=confidence,
                    reason="value_change",
                )
            )
            if belief.domain == "portion_model":
                evo = lifecycle.evolution_moment_for_portion(
                    old_value, value_dict, confidence_at=confidence, span_days=stats.span_days, occurred_on=ctx.now.date()
                )
                if await self._emit_moment(ctx.user_id, belief.id, evo, f"{candidate.distiller_id}:{candidate.distiller_version}"):
                    emitted += 1

        observed = [r.observed_at for r in evidence_records]
        belief.value_json = value_dict
        belief.value_schema_version = candidate.value.schema_version
        belief.confidence = confidence
        belief.consistency = candidate.consistency
        belief.last_reinforced_at = max(observed) if observed else ctx.now
        belief.evidence_span_days = stats.span_days
        belief.observation_count = stats.count
        belief.archived_at = None
        belief.status = lifecycle.status_for(confidence, domain=belief.domain, span_days=stats.span_days)
        await self.db.flush()
        await self._store_evidence(belief.id, evidence_records)
        return emitted

    async def _apply_status(self, belief: MemoryBelief, *, status_for: float, domain: str, stats, now: dt.datetime) -> None:
        new_status = lifecycle.status_for(status_for, domain=domain, span_days=stats.span_days)
        if new_status == belief.status:
            belief.confidence = status_for
            await self.db.flush()
            return
        await self._change_status(belief, new_status, status_for, now, reason="decay" if new_status == lifecycle.STATUS_ARCHIVED else "status_change")

    async def _change_status(self, belief: MemoryBelief, new_status: str, confidence: float, now: dt.datetime, *, reason: str) -> None:
        revision_no = await self.repo.next_revision_no(belief.id)
        await self.repo.add_revision(
            MemoryBeliefRevision(
                belief_id=belief.id,
                revision_no=revision_no,
                from_value_json=belief.value_json or {},
                to_value_json=belief.value_json or {},
                from_confidence=belief.confidence,
                to_confidence=confidence,
                reason=reason,
            )
        )
        belief.status = new_status
        belief.confidence = confidence
        belief.archived_at = now if new_status == lifecycle.STATUS_ARCHIVED else None
        await self.db.flush()

    async def _store_evidence(self, belief_id: int, records) -> None:
        rows = [
            MemoryEvidence(
                belief_id=belief_id,
                evidence_type=r.evidence_type,
                ref_table=r.ref_table,
                ref_id=r.ref_id,
                weight=SOURCE_WEIGHTS.get(r.evidence_type, 0.5),
                observed_at=r.observed_at,
                detail_json=r.detail,
            )
            for r in records
        ]
        await self.repo.replace_evidence(belief_id, rows)

    async def _emit_moment(self, user_id: int, belief_id: int | None, spec: MomentSpec, distiller_version: str) -> bool:
        if await self.repo.moment_beat_exists(user_id, spec.beat_key, belief_id):
            return False
        moment = MemoryMoment(
            user_id=user_id,
            belief_id=belief_id,
            moment_kind=spec.moment_kind,
            domain=spec.domain,
            beat_key=spec.beat_key,
            fact_json=spec.fact,
            confidence_at=spec.confidence_at,
            evidence_span_days=spec.evidence_span_days,
            occurred_on=spec.occurred_on,
            chapter_key=spec.occurred_on.strftime("%Y-%m"),
            distiller_version=distiller_version,
        )
        await self.repo.add_moment(moment)
        await self.repo.add_narrative(
            MemoryNarrative(
                moment_id=moment.id,
                locale="en",
                text=narrator.render_moment(spec.moment_kind, spec.domain, spec.fact, "en"),
                prompt_version=narrator.PROMPT_VERSION,
                model_version=narrator.MODEL_VERSION,
                source="template",
            )
        )
        return True

    # --- maintenance --------------------------------------------------------

    async def consolidate_user(self, user_id: int, *, now: dt.datetime | None = None) -> dict:
        """Recompute confidence for live beliefs against ``now`` (pure decay) and
        apply status transitions. Safe to run on a schedule."""
        if not settings.MEMORY_ENABLED:
            return {"skipped": True}
        now = now or _utcnow()
        beliefs = await self.repo.list_beliefs(user_id, include_archived=False)
        transitioned = 0
        for belief in beliefs:
            rows = await self.repo.list_evidence(belief.id)
            items = _to_items(rows)
            confidence = confidence_from_evidence(items, belief.domain, consistency=belief.consistency, now=now)
            stats = summarize_evidence(items)
            new_status = lifecycle.status_for(confidence, domain=belief.domain, span_days=stats.span_days)
            if new_status != belief.status:
                await self._change_status(
                    belief,
                    new_status,
                    confidence,
                    now,
                    reason="decay" if new_status == lifecycle.STATUS_ARCHIVED else "status_change",
                )
                transitioned += 1
            else:
                belief.confidence = confidence
                await self.db.flush()
        return {"beliefs_reviewed": len(beliefs), "transitioned": transitioned}

    # --- user feedback ------------------------------------------------------

    async def not_correct(self, belief_id: int, user_id: int, *, now: dt.datetime | None = None) -> MemoryBelief | None:
        """'Not correct': recoverable negative feedback. Lowers confidence and marks
        the belief disputed; it can re-materialize only on post-dispute evidence."""
        now = now or _utcnow()
        belief = await self.repo.get_belief(belief_id, user_id)
        if belief is None:
            return None
        old_confidence = belief.confidence
        belief.dispute_count += 1
        belief.disputed_at = now
        belief.status = lifecycle.STATUS_DISPUTED
        belief.confidence = min(belief.confidence, settings.MEMORY_ARCHIVE_FLOOR)
        revision_no = await self.repo.next_revision_no(belief.id)
        await self.repo.add_revision(
            MemoryBeliefRevision(
                belief_id=belief.id,
                revision_no=revision_no,
                from_value_json=belief.value_json or {},
                to_value_json=belief.value_json or {},
                from_confidence=old_confidence,
                to_confidence=belief.confidence,
                reason="dispute",
            )
        )
        await self.db.flush()
        return belief

    async def forget(self, belief_id: int, user_id: int, *, now: dt.datetime | None = None):
        """'Forget this': permanent suppression. Blocks re-derivation and hides the
        belief's moments."""
        now = now or _utcnow()
        belief = await self.repo.get_belief(belief_id, user_id)
        if belief is None:
            return None
        suppression = await self.repo.create_suppression(
            user_id, belief.domain, belief.concept, belief.concept_key, reason="forget"
        )
        belief.status = lifecycle.STATUS_ARCHIVED
        belief.archived_at = now
        await self.repo.hide_moments_for_belief(belief.id)
        await self.db.flush()
        return suppression

    async def unforget(self, suppression_id: int, user_id: int):
        return await self.repo.delete_suppression(suppression_id, user_id)


# --- pipeline integration ---------------------------------------------------


@dataclass(frozen=True)
class PortionHint:
    display_name: str
    grams: int
    grams_low: int
    grams_high: int
    confidence: float


@dataclass(frozen=True)
class PreferenceHint:
    display_name: str
    preference_type: str


@dataclass(frozen=True)
class MemoryEstimationHints:
    portion_hints: list[PortionHint] = field(default_factory=list)
    preference_hints: list[PreferenceHint] = field(default_factory=list)
    summary: str | None = None


class MemoryQueryService:
    """Read-only access to learned memories for the meal-estimation pipeline."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = MemoryRepository(db)

    async def get_estimation_hints(
        self,
        user_id: int,
        *,
        meal_name: str | None = None,
        item_names: list[str] | None = None,
    ) -> MemoryEstimationHints:
        if not settings.MEMORY_ENABLED:
            return MemoryEstimationHints()

        keys: set[str] = set()
        for name in [meal_name, *(item_names or [])]:
            key = canonicalize_food_name(name) if name else ""
            if key:
                keys.add(key[:160])

        portion_hints: list[PortionHint] = []
        portion_stmt = select(MemoryBelief).where(
            MemoryBelief.user_id == user_id,
            MemoryBelief.domain == "portion_model",
            MemoryBelief.status.in_(("active", "provisional")),
        )
        if keys:
            portion_stmt = portion_stmt.where(MemoryBelief.concept_key.in_(keys))
        else:
            # No meal name supplied: offer the strongest learned portions as general priors.
            portion_stmt = portion_stmt.order_by(MemoryBelief.confidence.desc()).limit(5)
        result = await self.db.execute(portion_stmt)
        for belief in result.scalars().all():
            value = belief.value_json or {}
            portion_hints.append(
                PortionHint(
                    display_name=value.get("display_name", ""),
                    grams=int(value.get("grams", 0) or 0),
                    grams_low=int(value.get("grams_low", 0) or 0),
                    grams_high=int(value.get("grams_high", 0) or 0),
                    confidence=belief.confidence,
                )
            )

        preference_hints: list[PreferenceHint] = []
        pref_result = await self.db.execute(
            select(MemoryBelief)
            .where(
                MemoryBelief.user_id == user_id,
                MemoryBelief.domain == "preference",
                MemoryBelief.status.in_(("active", "provisional")),
            )
            .order_by(MemoryBelief.confidence.desc())
            .limit(5)
        )
        for belief in pref_result.scalars().all():
            value = belief.value_json or {}
            preference_hints.append(
                PreferenceHint(display_name=value.get("display_name", ""), preference_type=value.get("preference_type", "regular"))
            )

        return MemoryEstimationHints(
            portion_hints=portion_hints,
            preference_hints=preference_hints,
            summary=self._render_summary(portion_hints, preference_hints),
        )

    @staticmethod
    def _render_summary(portion_hints: list[PortionHint], preference_hints: list[PreferenceHint]) -> str | None:
        lines: list[str] = []
        if portion_hints:
            portions = "; ".join(f"{h.display_name} ~{h.grams} g" for h in portion_hints if h.display_name)
            if portions:
                lines.append(f"Learned typical portions for this user: {portions}.")
        if preference_hints:
            foods = ", ".join(h.display_name for h in preference_hints if h.display_name)
            if foods:
                lines.append(f"Frequently confirmed foods: {foods}.")
        if not lines:
            return None
        return (
            " ".join(lines)
            + " Use these only as priors when the meal clearly matches; never override explicit quantities."
        )
