import datetime as dt
import json
import logging
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import desc, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.insights.detectors import PatternDetector
from app.insights.features import FeatureExtractor, FeatureSnapshot
from app.insights.patterns import VerifiedPattern
from app.insights.ranking import PatternRanker
from app.insights.versioning import EVENT_DOMAINS, DomainEvent
from app.models.daily_summary import DailySummary
from app.models.insight import (
    InsightNotificationPreference,
    ProactiveInsight,
    ProactiveInsightEvent,
)
from app.models.meal import Meal
from app.models.user import User
from app.proactive_insights.analytics import InsightAnalytics
from app.proactive_insights.candidates import (
    PERIODIC_TRIGGERS,
    CandidateFactory,
    InsightCandidate,
    ProactiveTrigger,
)
from app.proactive_insights.notifications import InsightNotificationService
from app.proactive_insights.quality import InsightQualityGate, QualityGateError
from app.proactive_insights.verbalizer import (
    PROACTIVE_INSIGHT_PROMPT_VERSION,
    ProactiveInsightVerbalizer,
)
from app.services.premium_service import PremiumService

logger = logging.getLogger("app.proactive_insights")


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


class CandidateEligibility:
    @staticmethod
    def passes(candidate: InsightCandidate) -> bool:
        return (
            candidate.confidence >= settings.PROACTIVE_INSIGHT_MIN_CONFIDENCE
            and candidate.significance >= settings.PROACTIVE_INSIGHT_MIN_SIGNIFICANCE
            and candidate.novelty >= settings.PROACTIVE_INSIGHT_MIN_NOVELTY
            and candidate.usefulness >= settings.PROACTIVE_INSIGHT_MIN_USEFULNESS
        )


class NotificationPolicy:
    @staticmethod
    def score(candidate: InsightCandidate) -> float:
        # Candidates are scored at creation, so verified recency is 1.0 here;
        # delivery applies the configured age window again before interruption.
        recency = 1.0
        score = (
            candidate.relevance * 0.25
            + candidate.urgency * 0.18
            + candidate.novelty * 0.20
            + candidate.confidence * 0.22
            + recency * 0.15
            - candidate.interruption_cost * 0.18
        )
        return round(max(0.0, min(1.0, score)), 4)

    async def state(
        self,
        db: AsyncSession,
        candidate: InsightCandidate,
        *,
        now: dt.datetime,
    ) -> tuple[float, str, dt.datetime | None]:
        score = self.score(candidate)
        if score < settings.PROACTIVE_NOTIFICATION_MIN_SCORE:
            return score, "not_eligible", None
        return score, "ready", now


class ProactiveInsightService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        verbalizer: ProactiveInsightVerbalizer | None = None,
        quality_gate: InsightQualityGate | None = None,
    ):
        self.db = db
        self.verbalizer = verbalizer or ProactiveInsightVerbalizer()
        self.quality_gate = quality_gate or InsightQualityGate()
        self.ranker = PatternRanker()
        self.detectors = [detector_cls() for detector_cls in PatternDetector.registry]
        self.notification_policy = NotificationPolicy()

    @staticmethod
    def event_key(
        user_id: int,
        trigger: str,
        *,
        affected_date: dt.date | None,
        source_versions: dict[str, int],
        discriminator: str | None = None,
    ) -> str:
        return _hash(
            {
                "user_id": user_id,
                "trigger": trigger,
                "affected_date": affected_date,
                "source_versions": source_versions,
                "discriminator": discriminator,
            }
        )

    @classmethod
    async def stage_event(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        trigger: str,
        affected_date: dt.date | None,
        source_versions: dict[str, int],
        payload: dict[str, Any] | None = None,
        discriminator: str | None = None,
    ) -> ProactiveInsightEvent:
        event_id = cls.event_key(
            user_id,
            trigger,
            affected_date=affected_date,
            source_versions=source_versions,
            discriminator=discriminator,
        )
        existing = await db.scalar(select(ProactiveInsightEvent).where(ProactiveInsightEvent.event_id == event_id))
        if existing is not None:
            return existing
        event = ProactiveInsightEvent(
            event_id=event_id,
            user_id=user_id,
            trigger=trigger,
            affected_date=affected_date,
            source_versions_json=source_versions,
            payload_json=payload or {},
            status="pending",
        )
        try:
            async with db.begin_nested():
                db.add(event)
                await db.flush()
        except IntegrityError:
            existing = await db.scalar(
                select(ProactiveInsightEvent).where(ProactiveInsightEvent.event_id == event_id)
            )
            if existing is not None:
                return existing
            raise
        return event

    async def _advisory_lock(self, event_id: str) -> None:
        if self.db.get_bind().dialect.name == "postgresql":
            lock_key = int(_hash(event_id)[:15], 16)
            await self.db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

    @staticmethod
    def _period_days(trigger: str) -> int:
        return {
            ProactiveTrigger.DAILY.value: 14,
            ProactiveTrigger.WEEKLY.value: 30,
            ProactiveTrigger.MONTHLY.value: 90,
        }.get(trigger, 30)

    @staticmethod
    def _event_domains(trigger: str) -> set[str] | None:
        if trigger in PERIODIC_TRIGGERS:
            return None
        try:
            domain_event = DomainEvent(trigger)
        except ValueError:
            return None
        return {domain.value for domain in EVENT_DOMAINS[domain_event]}

    async def _load_features(
        self, user: User, *, end_date: dt.date, period_days: int
    ) -> tuple[FeatureSnapshot, list[Meal]]:
        start_date = end_date - dt.timedelta(days=period_days - 1)
        start_at = dt.datetime.combine(start_date, dt.time.min, tzinfo=dt.UTC)
        end_at = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)
        summaries = list(
            (
                await self.db.scalars(
                    select(DailySummary).where(
                        DailySummary.user_id == user.id,
                        DailySummary.date >= start_date,
                        DailySummary.date <= end_date,
                    )
                )
            ).all()
        )
        meals = list(
            (
                await self.db.scalars(
                    select(Meal).where(
                        Meal.user_id == user.id,
                        Meal.created_at >= start_at,
                        Meal.created_at < end_at,
                    )
                )
            ).all()
        )
        snapshot = FeatureExtractor.extract(
            period_days=period_days,
            end_date=end_date,
            calorie_goal=user.daily_calorie_goal,
            summaries=summaries,
            meals=meals,
            current_weight_kg=user.weight_kg,
            protein_goal_g=user.daily_protein_goal,
            carbs_goal_g=user.daily_carbs_goal,
            fat_goal_g=user.daily_fat_goal,
        )
        return snapshot, meals

    async def candidates_for(
        self,
        event: ProactiveInsightEvent,
        user: User,
        *,
        today: dt.date | None = None,
        now: dt.datetime | None = None,
    ) -> list[InsightCandidate]:
        now = now or dt.datetime.now(dt.UTC)
        if today is not None:
            end_date = today
        elif event.trigger in PERIODIC_TRIGGERS and event.affected_date is not None:
            end_date = event.affected_date
        else:
            end_date = dt.date.today()
        period_days = self._period_days(event.trigger)
        snapshot, meals = await self._load_features(user, end_date=end_date, period_days=period_days)
        domains = self._event_domains(event.trigger)

        detected: list[tuple[PatternDetector, VerifiedPattern]] = []
        for detector in self.detectors:
            if domains is not None and not detector.dependencies.intersection(domains):
                continue
            detected.extend((detector, pattern) for pattern in detector.detect(snapshot))
        ranked_patterns = self.ranker.rank(
            [pattern for _, pattern in detected],
            limit=settings.PROACTIVE_INSIGHT_MAX_CANDIDATES_PER_EVENT,
        )
        detector_by_pattern = {id(pattern): detector for detector, pattern in detected}
        candidates = [
            CandidateFactory.from_pattern(
                pattern,
                user_id=user.id,
                detector_id=detector_by_pattern[id(pattern)].detector_id,
                source_trigger=event.trigger,
                period_start=snapshot.start_date,
                period_end=snapshot.end_date,
                now=now,
            )
            for pattern in ranked_patterns
        ]

        meal_event = event.trigger in {
            DomainEvent.MEAL_CREATED.value,
            DomainEvent.MEAL_UPDATED.value,
            DomainEvent.MEAL_CORRECTED.value,
        }
        if meal_event or event.trigger in PERIODIC_TRIGGERS:
            current_day = next((day for day in snapshot.days if day.date == end_date), None)
            if current_day is not None:
                milestone = CandidateFactory.calorie_milestone(
                    user_id=user.id,
                    source_trigger=event.trigger,
                    day=current_day,
                    now=now,
                )
                if milestone is not None:
                    candidates.append(milestone)
            repeated = CandidateFactory.repeated_meal(
                user_id=user.id,
                source_trigger=event.trigger,
                meals=meals,
                period_start=snapshot.start_date,
                period_end=snapshot.end_date,
                now=now,
            )
            if repeated is not None:
                candidates.append(repeated)

        return sorted(
            candidates,
            key=lambda item: (
                -(item.confidence * item.significance * item.novelty * item.relevance),
                item.candidate_id,
            ),
        )[: settings.PROACTIVE_INSIGHT_MAX_CANDIDATES_PER_EVENT]

    @staticmethod
    def _cooldown_days(candidate: InsightCandidate) -> int:
        return settings.proactive_insight_type_cooldowns.get(
            candidate.type,
            1
            if candidate.trigger == ProactiveTrigger.CALORIE_MILESTONE.value
            else settings.PROACTIVE_INSIGHT_COOLDOWN_DAYS,
        )

    async def _deduplicated(
        self, candidate: InsightCandidate, *, now: dt.datetime
    ) -> tuple[bool, ProactiveInsight | None]:
        exact = await self.db.scalar(
            select(ProactiveInsight).where(ProactiveInsight.candidate_id == candidate.candidate_id)
        )
        if exact is not None:
            return True, exact

        latest_type = await self.db.scalar(
            select(ProactiveInsight)
            .where(
                ProactiveInsight.user_id == candidate.user_id,
                ProactiveInsight.type == candidate.type,
            )
            .order_by(desc(ProactiveInsight.created_at))
            .limit(1)
        )
        if latest_type is not None:
            created_at = latest_type.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=dt.UTC)
            if now < created_at + dt.timedelta(days=self._cooldown_days(candidate)):
                return True, latest_type

        semantic = await self.db.scalar(
            select(ProactiveInsight)
            .where(
                ProactiveInsight.user_id == candidate.user_id,
                ProactiveInsight.dedup_key == candidate.dedup_key,
                ProactiveInsight.created_at >= now - dt.timedelta(days=30),
            )
            .order_by(desc(ProactiveInsight.created_at))
            .limit(1)
        )
        if semantic is not None and candidate.significance < semantic.significance + 0.15:
            return True, semantic
        return False, latest_type

    async def _recent_copy(self, user_id: int) -> list[str]:
        rows = list(
            (
                await self.db.scalars(
                    select(ProactiveInsight)
                    .where(ProactiveInsight.user_id == user_id)
                    .order_by(desc(ProactiveInsight.created_at))
                    .limit(20)
                )
            ).all()
        )
        return [f"{row.title} {row.body}" for row in rows]

    async def process_event(
        self,
        event_id: str,
        *,
        locale: str = "en",
        today: dt.date | None = None,
        now: dt.datetime | None = None,
    ) -> dict[str, int | str]:
        now = now or dt.datetime.now(dt.UTC)
        await self._advisory_lock(event_id)
        event = await self.db.scalar(select(ProactiveInsightEvent).where(ProactiveInsightEvent.event_id == event_id))
        if event is None:
            return {"status": "missing", "persisted": 0}
        if event.status == "completed":
            return {"status": "completed", "persisted": int(event.result_json.get("persisted", 0))}
        if event.status == "processing":
            return {"status": "processing", "persisted": 0}

        event.status = "processing"
        event.attempts += 1
        event.error_code = None
        await self.db.flush()
        user = await self.db.get(User, event.user_id)
        if user is None:
            event.status = "completed"
            event.processed_at = now
            event.result_json = {"persisted": 0, "reason": "user_missing"}
            await self.db.flush()
            return {"status": "completed", "persisted": 0}
        premium = await PremiumService(self.db).get_premium_status(user)
        if not premium.is_premium:
            event.status = "completed"
            event.processed_at = now
            event.result_json = {"persisted": 0, "reason": "premium_required"}
            await self.db.flush()
            return {"status": "completed", "persisted": 0}

        candidates = await self.candidates_for(event, user, today=today, now=now)
        counts = {"candidates": len(candidates), "ineligible": 0, "deduplicated": 0, "rejected": 0, "persisted": 0}
        errors = 0
        for candidate in candidates:
            if not CandidateEligibility.passes(candidate):
                counts["ineligible"] += 1
                continue
            duplicate, related = await self._deduplicated(candidate, now=now)
            if duplicate:
                counts["deduplicated"] += 1
                continue
            try:
                generated = await self.verbalizer.verbalize(candidate, locale=locale)
                self.quality_gate.validate(
                    candidate,
                    title=generated.title,
                    body=generated.body,
                    evidence_refs=generated.evidence_refs,
                    recent_copy=await self._recent_copy(candidate.user_id),
                )
            except QualityGateError as exc:
                counts["rejected"] += 1
                logger.info(
                    "event=proactive_insight_rejected event_id=%s candidate_id=%s reason=%s",
                    event.event_id,
                    candidate.candidate_id,
                    str(exc),
                )
                continue
            except Exception as exc:
                errors += 1
                logger.warning(
                    "event=proactive_insight_verbalization_failed event_id=%s candidate_id=%s error=%s",
                    event.event_id,
                    candidate.candidate_id,
                    type(exc).__name__,
                )
                continue

            score, notification_status, notification_ready_at = await self.notification_policy.state(
                self.db, candidate, now=now
            )
            insight_id = f"insight_{_hash(candidate.candidate_id)[:40]}"
            insight = ProactiveInsight(
                id=insight_id,
                candidate_id=candidate.candidate_id,
                user_id=candidate.user_id,
                type=candidate.type,
                category=candidate.category,
                trigger=candidate.trigger,
                title=generated.title.strip(),
                body=generated.body.strip(),
                evidence_json={"evidence": candidate.evidence, "metrics": candidate.metrics},
                confidence=candidate.confidence,
                novelty=candidate.novelty,
                relevance=candidate.relevance,
                significance=candidate.significance,
                direction=candidate.direction,
                dedup_key=candidate.dedup_key,
                related_insight_id=related.id if related is not None else None,
                notification_score=score,
                notification_status=notification_status,
                notification_ready_at=notification_ready_at,
                model_version=settings.PROACTIVE_INSIGHT_MODEL,
                prompt_version=PROACTIVE_INSIGHT_PROMPT_VERSION,
                created_at=now,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(insight)
                    await self.db.flush()
            except IntegrityError:
                counts["deduplicated"] += 1
                continue
            if (
                related is not None
                and related.dedup_key == candidate.dedup_key
                and candidate.significance >= related.significance + 0.15
            ):
                related.superseded_at = now
                if related.notification_sent_at is None:
                    related.notification_status = "superseded"
            await InsightAnalytics(self.db).record(
                user_id=user.id,
                event_name="insight_created",
                insight=insight,
                event_id=f"{insight.id}:insight_created",
                now=now,
            )
            await InsightNotificationService(self.db).schedule(
                insight, user, now=now
            )
            counts["persisted"] += 1

        event.result_json = counts
        event.processed_at = now
        if errors:
            event.status = "failed"
            event.error_code = "verbalization_failed"
        else:
            event.status = "completed"
        await self.db.flush()
        return {"status": event.status, "persisted": counts["persisted"]}

    async def stage_periodic(self, period: str, *, evaluation_date: dt.date | None = None) -> list[str]:
        trigger = {
            "daily": ProactiveTrigger.DAILY.value,
            "weekly": ProactiveTrigger.WEEKLY.value,
            "monthly": ProactiveTrigger.MONTHLY.value,
        }.get(period)
        if trigger is None:
            raise ValueError(f"Unsupported proactive insight period: {period}")
        evaluation_date = evaluation_date or dt.date.today()
        users = list(
            (
                await self.db.scalars(
                    select(User).where(
                        User.access_status == "active",
                        User.deletion_in_progress.is_(False),
                        User.is_premium.is_(True),
                    )
                )
            ).all()
        )
        event_ids = []
        for user in users:
            discriminator = {
                "daily": evaluation_date.isoformat(),
                "weekly": f"{evaluation_date.isocalendar().year}-W{evaluation_date.isocalendar().week:02d}",
                "monthly": evaluation_date.strftime("%Y-%m"),
            }[period]
            event = await self.stage_event(
                self.db,
                user_id=user.id,
                trigger=trigger,
                affected_date=evaluation_date,
                source_versions={},
                payload={"period": period, "evaluation_date": evaluation_date.isoformat()},
                discriminator=discriminator,
            )
            event_ids.append(event.event_id)
        return event_ids

    async def stage_due_periodic(self, *, now: dt.datetime | None = None) -> list[str]:
        """Stage local-time evaluations; stable event IDs make repeated sweeps safe."""
        now = now or dt.datetime.now(dt.UTC)
        users = list(
            (
                await self.db.scalars(
                    select(User).where(
                        User.access_status == "active",
                        User.deletion_in_progress.is_(False),
                        User.is_premium.is_(True),
                    )
                )
            ).all()
        )
        preferences = {
            row.user_id: row
            for row in (
                await self.db.scalars(select(InsightNotificationPreference))
            ).all()
        }
        event_ids: list[str] = []
        for user in users:
            timezone = preferences.get(user.id).timezone if user.id in preferences else "UTC"
            try:
                local = now.astimezone(ZoneInfo(timezone))
            except ZoneInfoNotFoundError:
                local = now.astimezone(dt.UTC)
                timezone = "UTC"
            due: list[str] = []
            if local.hour >= 18:
                due.append("daily")
            if local.weekday() == 6 and local.hour >= 18:
                due.append("weekly")
            if local.day == 1 and local.hour >= 10:
                due.append("monthly")
            for period in due:
                trigger = {
                    "daily": ProactiveTrigger.DAILY.value,
                    "weekly": ProactiveTrigger.WEEKLY.value,
                    "monthly": ProactiveTrigger.MONTHLY.value,
                }[period]
                discriminator = {
                    "daily": local.date().isoformat(),
                    "weekly": f"{local.isocalendar().year}-W{local.isocalendar().week:02d}",
                    "monthly": local.strftime("%Y-%m"),
                }[period]
                event = await self.stage_event(
                    self.db,
                    user_id=user.id,
                    trigger=trigger,
                    affected_date=local.date(),
                    source_versions={},
                    payload={
                        "period": period,
                        "evaluation_date": local.date().isoformat(),
                        "evaluation_timezone": timezone,
                    },
                    discriminator=discriminator,
                )
                event_ids.append(event.event_id)
        return event_ids
