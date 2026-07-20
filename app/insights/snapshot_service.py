import asyncio
import datetime as dt
import json
import logging
import time
from hashlib import sha256
from typing import Any

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.insights import STORY_VERBALIZATION_PROMPT_VERSION
from app.ai.providers.openrouter import OpenRouterProvider
from app.core.config import settings
from app.insights.comparison import PatternChange, PatternComparer, payload_hash
from app.insights.detectors import PatternDetector
from app.insights.features import FeatureExtractor, FeatureSnapshot
from app.insights.patterns import VerifiedPattern
from app.insights.ranking import PatternRanker
from app.insights.versioning import InsightVersionService
from app.models.daily_summary import DailySummary
from app.models.insight import DetectedPattern, InsightSnapshot
from app.models.meal import Meal
from app.models.user import User
from app.schemas.insights import InsightStoriesResponse, InsightStory

logger = logging.getLogger("app.insights.snapshots")
_local_locks: dict[tuple[int, str, str], asyncio.Lock] = {}


class InsightSnapshotService:
    def __init__(
        self,
        db: AsyncSession,
        provider: OpenRouterProvider | None = None,
        *,
        debounce_seconds: int | None = None,
    ):
        self.db = db
        self.provider = provider or OpenRouterProvider()
        self.debounce_seconds = (
            settings.INSIGHT_RECOMPUTE_DEBOUNCE_SECONDS if debounce_seconds is None else debounce_seconds
        )
        self.comparer = PatternComparer()
        self.ranker = PatternRanker()
        self.detectors = [detector_cls() for detector_cls in PatternDetector.registry]

    @staticmethod
    def _locale(locale: str) -> str:
        return OpenRouterProvider._insight_locale(locale)

    @staticmethod
    def _hash(value: Any) -> str:
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    @property
    def detector_version(self) -> str:
        versions = [(detector.detector_id, detector.detector_version) for detector in self.detectors]
        return self._hash(versions)[:16]

    @property
    def detector_dependencies(self) -> dict[str, list[str]]:
        return {detector.detector_id: sorted(detector.dependencies) for detector in self.detectors}

    def _relevant_versions(self, versions: dict[str, int]) -> dict[str, int]:
        relevant = {dependency for detector in self.detectors for dependency in detector.dependencies}
        return {key: versions[key] for key in sorted(relevant)}

    @staticmethod
    def _period(scope: str, today: dt.date, period_start: dt.date | None) -> tuple[dt.date, dt.date]:
        if scope == "rolling_30d":
            return today - dt.timedelta(days=29), today
        if scope == "weekly_current":
            start = today - dt.timedelta(days=today.weekday())
            return start, today
        if scope == "weekly_closed":
            if period_start is None:
                raise ValueError("period_start is required for weekly_closed")
            if period_start.weekday() != 0:
                raise ValueError("weekly_closed period_start must be a Monday")
            end = period_start + dt.timedelta(days=6)
            if end >= today:
                raise ValueError("weekly_closed period must end before today")
            return period_start, end
        raise ValueError(f"Unsupported insight scope: {scope}")

    async def _load_features(self, user: User, start: dt.date, end: dt.date) -> FeatureSnapshot:
        start_datetime = dt.datetime.combine(start, dt.time.min, tzinfo=dt.UTC)
        end_datetime = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)
        summaries_result = await self.db.execute(
            select(DailySummary).where(
                DailySummary.user_id == user.id,
                DailySummary.date >= start,
                DailySummary.date <= end,
            )
        )
        meals_result = await self.db.execute(
            select(Meal).where(
                Meal.user_id == user.id,
                Meal.created_at >= start_datetime,
                Meal.created_at < end_datetime,
            )
        )
        return FeatureExtractor.extract(
            period_days=(end - start).days + 1,
            end_date=end,
            calorie_goal=user.daily_calorie_goal,
            summaries=list(summaries_result.scalars().all()),
            meals=list(meals_result.scalars().all()),
            current_weight_kg=user.weight_kg,
            protein_goal_g=user.daily_protein_goal,
            carbs_goal_g=user.daily_carbs_goal,
            fat_goal_g=user.daily_fat_goal,
        )

    async def _latest_snapshot(
        self,
        user_id: int,
        scope: str,
        locale: str,
        period_start: dt.date | None = None,
    ) -> InsightSnapshot | None:
        stmt = select(InsightSnapshot).where(
            InsightSnapshot.user_id == user_id,
            InsightSnapshot.insight_scope == scope,
            InsightSnapshot.locale == locale,
            InsightSnapshot.status.in_(("fresh", "stale", "generating")),
        )
        if scope == "weekly_closed" and period_start is not None:
            stmt = stmt.where(InsightSnapshot.period_start == period_start)
        result = await self.db.execute(stmt.order_by(desc(InsightSnapshot.created_at), desc(InsightSnapshot.id)).limit(1))
        return result.scalar_one_or_none()

    def _is_valid(
        self,
        snapshot: InsightSnapshot,
        versions: dict[str, int],
        scope: str,
        period_start: dt.date,
        period_end: dt.date,
    ) -> bool:
        code_matches = (
            snapshot.detector_version == self.detector_version
            and snapshot.prompt_version == STORY_VERBALIZATION_PROMPT_VERSION
            and snapshot.model_version == settings.OPENROUTER_TEXT_MODEL
        )
        if not code_matches or snapshot.status != "fresh":
            return False
        if scope.startswith("weekly"):
            return (
                snapshot.stale_at is None
                and snapshot.period_start == period_start
                and snapshot.period_end == period_end
            )
        return (
            snapshot.source_versions_json == versions
            and snapshot.period_start == period_start
            and snapshot.period_end == period_end
        )

    async def _try_distributed_lock(self, user_id: int, scope: str, locale: str) -> bool:
        bind = self.db.get_bind()
        if bind.dialect.name != "postgresql":
            return True
        lock_key = int(self._hash([user_id, scope, locale])[:15], 16)
        result = await self.db.execute(text("SELECT pg_try_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
        return bool(result.scalar())

    def _is_debouncing(self, snapshot: InsightSnapshot | None) -> bool:
        if snapshot is None or snapshot.stale_at is None or self.debounce_seconds <= 0:
            return False
        stale_at = snapshot.stale_at
        if stale_at.tzinfo is None:
            stale_at = stale_at.replace(tzinfo=dt.UTC)
        return dt.datetime.now(dt.UTC) < stale_at + dt.timedelta(seconds=self.debounce_seconds)

    @staticmethod
    def _response(snapshot: InsightSnapshot, *, update_pending: bool = False) -> InsightStoriesResponse:
        return InsightStoriesResponse(
            snapshot_id=snapshot.snapshot_id,
            scope=snapshot.insight_scope,
            source_data_version=snapshot.source_data_version,
            status="stale" if update_pending else snapshot.status,
            update_pending=update_pending,
            generated_at=snapshot.generated_at,
            stories=[InsightStory.model_validate(item) for item in snapshot.insights_json],
            ranking_metadata=snapshot.ranking_metadata_json,
        )

    async def get_stories(
        self,
        user: User,
        *,
        scope: str = "rolling_30d",
        locale: str = "en",
        period_start: dt.date | None = None,
        today: dt.date | None = None,
    ) -> InsightStoriesResponse:
        locale = self._locale(locale)
        today = today or dt.date.today()
        start, end = self._period(scope, today, period_start)
        all_versions = await InsightVersionService(self.db).current(user.id)
        relevant_versions = self._relevant_versions(all_versions)
        previous = await self._latest_snapshot(user.id, scope, locale, start)
        if previous and self._is_valid(previous, relevant_versions, scope, start, end):
            logger.info("event=snapshot_hit user_id=%s scope=%s snapshot_id=%s", user.id, scope, previous.snapshot_id)
            return self._response(previous)

        logger.info("event=snapshot_stale user_id=%s scope=%s", user.id, scope)
        if self._is_debouncing(previous):
            logger.info("event=snapshot_refresh_debounced user_id=%s scope=%s", user.id, scope)
            return self._response(previous, update_pending=True)
        lock = _local_locks.setdefault((user.id, scope, locale), asyncio.Lock())
        if lock.locked() and previous is not None:
            return self._response(previous, update_pending=True)
        async with lock:
            previous = await self._latest_snapshot(user.id, scope, locale, start)
            all_versions = await InsightVersionService(self.db).current(user.id)
            relevant_versions = self._relevant_versions(all_versions)
            if previous and self._is_valid(previous, relevant_versions, scope, start, end):
                return self._response(previous)
            if not await self._try_distributed_lock(user.id, scope, locale):
                if previous is not None:
                    return self._response(previous, update_pending=True)
                return InsightStoriesResponse(
                    scope=scope,
                    source_data_version=self._hash(relevant_versions),
                    status="generating",
                    update_pending=True,
                )
            return await self._recompute(
                user=user,
                scope=scope,
                locale=locale,
                start=start,
                end=end,
                versions=relevant_versions,
                previous=previous,
            )

    async def _active_patterns(self, user_id: int, scope: str, start: dt.date) -> list[DetectedPattern]:
        stmt = select(DetectedPattern).where(
            DetectedPattern.user_id == user_id,
            DetectedPattern.scope == scope,
            DetectedPattern.superseded_at.is_(None),
        )
        if scope == "weekly_closed":
            stmt = stmt.where(DetectedPattern.period_start == start)
        result = await self.db.execute(stmt.order_by(desc(DetectedPattern.created_at), desc(DetectedPattern.id)))
        deduplicated: dict[tuple[str, str], DetectedPattern] = {}
        for row in result.scalars().all():
            deduplicated.setdefault((row.detector_id, row.pattern_key), row)
        return list(deduplicated.values())

    @staticmethod
    def _as_pattern(row: DetectedPattern) -> VerifiedPattern:
        return VerifiedPattern(
            id=row.pattern_key,
            category=row.category,
            confidence=row.confidence,
            priority=row.priority,
            payload=row.payload_json,
            novelty=row.novelty_score,
            effect_size=row.effect_size,
            concept=row.detector_id,
        )

    @staticmethod
    def _story_id(user_id: int, detector_id: str, pattern_key: str, scope: str, start: dt.date) -> str:
        stable_period = start.isoformat() if scope.startswith("weekly") else "rolling"
        raw = f"{user_id}:{detector_id}:{pattern_key}:{scope}:{stable_period}"
        return sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def _direction(pattern: VerifiedPattern) -> str:
        explicit = pattern.payload.get("direction")
        if explicit in {"improved"}:
            return "positive"
        if explicit in {"worsened"}:
            return "negative"
        if pattern.id == "goal_adherence_change":
            return "positive" if pattern.payload.get("adherence_rate_change", 0) > 0 else "negative"
        return "neutral"

    @staticmethod
    def _category(pattern: VerifiedPattern) -> str:
        return {
            "ai_accuracy": "accuracy",
            "learning": "progress",
            "consistency": "consistency",
            "logging": "consistency",
            "macros": "macros",
            "meal_distribution": "meals",
            "timing": "meals",
            "activity": "activity",
            "hydration": "water",
            "calories": "progress",
            "improvement": "progress",
        }.get(pattern.category, "progress")

    def _evidence(self, pattern: VerifiedPattern, locale: str) -> list[dict[str, str]]:
        rendered = OpenRouterProvider._insight_evidence(pattern, locale=locale)
        default_label = "Dato verificato" if locale == "it" else "Verified fact"
        evidence = []
        for item in rendered[:6]:
            if ":" in item:
                label, value = item.split(":", 1)
                evidence.append({"label": label.strip(), "value": value.strip()})
            else:
                evidence.append({"label": default_label, "value": item})
        return evidence

    async def _persist_pattern(
        self,
        *,
        user_id: int,
        detector: PatternDetector,
        pattern: VerifiedPattern,
        scope: str,
        start: dt.date,
        end: dt.date,
        versions: dict[str, int],
        change: PatternChange,
        previous: DetectedPattern | None,
        locale: str,
    ) -> DetectedPattern:
        now = dt.datetime.now(dt.UTC)
        if previous is not None:
            previous.superseded_at = now
        row = DetectedPattern(
            user_id=user_id,
            detector_id=detector.detector_id,
            detector_version=detector.detector_version,
            scope=scope,
            period_start=start,
            period_end=end,
            source_versions_json={key: versions[key] for key in detector.dependencies},
            pattern_key=pattern.id,
            category=pattern.category,
            confidence=pattern.confidence,
            priority=pattern.priority,
            novelty_score=pattern.novelty,
            effect_size=pattern.effect_size,
            payload_json=pattern.payload,
            evidence_json=self._evidence(pattern, locale),
            comparison_status=change.value,
            payload_hash=payload_hash(pattern.payload),
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def _recompute(
        self,
        *,
        user: User,
        scope: str,
        locale: str,
        start: dt.date,
        end: dt.date,
        versions: dict[str, int],
        previous: InsightSnapshot | None,
    ) -> InsightStoriesResponse:
        feature_snapshot = await self._load_features(user, start, end)
        previous_rows = await self._active_patterns(user.id, scope, start)
        previous_by_detector: dict[str, dict[str, DetectedPattern]] = {}
        for row in previous_rows:
            previous_by_detector.setdefault(row.detector_id, {})[row.pattern_key] = row
        previous_versions = previous.source_versions_json if previous else {}
        changed_domains = {
            key for key, value in versions.items() if previous_versions.get(key) != value
        }
        if (
            previous is None
            or previous.detector_version != self.detector_version
            or previous.period_start != start
            or previous.period_end != end
        ):
            changed_domains = set(versions)

        active: list[tuple[PatternDetector, VerifiedPattern, PatternChange]] = []
        now = dt.datetime.now(dt.UTC)
        for detector in self.detectors:
            old_rows = previous_by_detector.get(detector.detector_id, {})
            should_run = not old_rows or bool(detector.dependencies.intersection(changed_domains))
            if not should_run:
                active.extend(
                    (detector, self._as_pattern(row), PatternChange.UNCHANGED)
                    for row in old_rows.values()
                )
                logger.info("event=detector_unchanged user_id=%s detector_id=%s reason=unaffected", user.id, detector.detector_id)
                continue
            detected = detector.detect(feature_snapshot)
            detected_keys = {pattern.id for pattern in detected}
            replacement_row = next(iter(old_rows.values())) if len(old_rows) == 1 and len(detected) == 1 else None
            used_old_ids: set[int] = set()
            for current in detected:
                old_row = old_rows.get(current.id)
                if old_row is None and replacement_row is not None:
                    old_row = replacement_row
                if old_row is not None:
                    used_old_ids.add(old_row.id)
                old_payload = (
                    {"pattern_key": old_row.pattern_key, "payload_json": old_row.payload_json}
                    if old_row is not None
                    else None
                )
                change = self.comparer.compare(old_payload, current)
                # A failed verbalization may already have persisted the new pattern.
                # Do not mistake that row for a story the previous snapshot voiced.
                if (
                    change == PatternChange.UNCHANGED
                    and previous is not None
                    and old_row is not None
                    and old_row.created_at >= previous.generated_at
                    and old_row.comparison_status
                    in {PatternChange.MATERIAL.value, PatternChange.NEW.value, PatternChange.REPLACED.value}
                ):
                    change = PatternChange.MATERIAL
                logger.info("event=detector_recomputed user_id=%s detector_id=%s change=%s", user.id, detector.detector_id, change.value)
                if change in {PatternChange.MATERIAL, PatternChange.NEW, PatternChange.REPLACED}:
                    logger.info("event=detector_material_change user_id=%s detector_id=%s", user.id, detector.detector_id)
                await self._persist_pattern(
                    user_id=user.id,
                    detector=detector,
                    pattern=current,
                    scope=scope,
                    start=start,
                    end=end,
                    versions=versions,
                    change=change,
                    previous=old_row,
                    locale=locale,
                )
                active.append((detector, current, change))

            for pattern_key, old_row in old_rows.items():
                if old_row.id in used_old_ids or pattern_key in detected_keys:
                    continue
                old_row.superseded_at = now
                logger.info("event=story_removed user_id=%s detector_id=%s pattern_key=%s", user.id, detector.detector_id, pattern_key)

        ranked = self.ranker.rank([pattern for _, pattern, _ in active], limit=8)
        detector_for_pattern = {id(pattern): (detector, change) for detector, pattern, change in active}
        diverse: list[VerifiedPattern] = []
        categories: set[str] = set()
        for pattern in ranked:
            category = self._category(pattern)
            if category in categories:
                continue
            diverse.append(pattern)
            categories.add(category)
            if len(diverse) == 4:
                break

        candidates = []
        for pattern in diverse:
            detector, change = detector_for_pattern[id(pattern)]
            candidates.append(
                {
                    "story_id": self._story_id(user.id, detector.detector_id, pattern.id, scope, start),
                    "detector": detector,
                    "pattern": pattern,
                    "change": change,
                    "score": self.ranker.score(pattern),
                }
            )
        previous_order = previous.ranking_metadata_json.get("story_order", []) if previous else []
        order_index = {story_id: index for index, story_id in enumerate(previous_order)}
        candidates.sort(key=lambda item: (order_index.get(item["story_id"], 999), -item["score"], item["story_id"]))

        previous_copy = {item["story_id"]: item for item in previous.insights_json} if previous else {}
        final_by_id: dict[str, dict] = {}
        to_generate: list[dict] = []
        for item in candidates:
            story_id = item["story_id"]
            pattern = item["pattern"]
            if story_id in previous_copy and item["change"] in {PatternChange.UNCHANGED, PatternChange.MINIMAL}:
                final_by_id[story_id] = previous_copy[story_id]
                logger.info("event=llm_verbalization_reused user_id=%s story_id=%s", user.id, story_id)
                continue
            if story_id in previous_copy:
                logger.info("event=story_replaced user_id=%s story_id=%s", user.id, story_id)
            to_generate.append(
                {
                    "story_id": story_id,
                    "detector_id": item["detector"].detector_id,
                    "pattern_key": pattern.id,
                    "confidence_label": OpenRouterProvider._insight_confidence(pattern.confidence),
                    "metric": OpenRouterProvider._insight_metric(pattern, locale=locale),
                    "evidence": self._evidence(pattern, locale),
                    "category": self._category(pattern),
                    "direction": self._direction(pattern),
                    "payload": pattern.verified_dict(),
                }
            )

        try:
            if to_generate:
                logger.info("event=llm_verbalization_called user_id=%s story_count=%s", user.id, len(to_generate))
                started_at = time.perf_counter()
                generated = await self.provider.verbalize_insight_stories(to_generate, locale=locale)
                logger.info(
                    "event=llm_verbalization_batch_completed user_id=%s story_count=%s duration_ms=%s",
                    user.id,
                    len(generated),
                    int((time.perf_counter() - started_at) * 1000),
                )
                final_by_id.update({story.story_id: story.model_dump(mode="json") for story in generated})
            ordered_stories = [final_by_id[item["story_id"]] for item in candidates if item["story_id"] in final_by_id]
        except Exception as exc:
            logger.exception("event=snapshot_failed user_id=%s scope=%s error_type=%s", user.id, scope, type(exc).__name__)
            await self._record_failed_snapshot(user.id, scope, locale, start, end, versions, exc)
            if previous is not None:
                return self._response(previous, update_pending=True)
            return InsightStoriesResponse(
                scope=scope,
                source_data_version=self._hash(versions),
                status="failed",
                stories=[],
            )

        source_data_version = self._hash(versions)
        generation_key = self._hash(
            [user.id, scope, locale, source_data_version, self.detector_version, STORY_VERBALIZATION_PROMPT_VERSION, settings.OPENROUTER_TEXT_MODEL]
        )
        metadata = {
            "story_order": [item["story_id"] for item in candidates],
            "scores": {item["story_id"]: item["score"] for item in candidates},
            "changes": {item["story_id"]: item["change"].value for item in candidates},
            "detector_dependencies": self.detector_dependencies,
            "changed_domains": sorted(changed_domains),
            "generated_story_count": len(to_generate),
            "reused_story_count": len(ordered_stories) - len(to_generate),
        }
        snapshot = InsightSnapshot(
            snapshot_id=generation_key,
            generation_key=generation_key,
            user_id=user.id,
            insight_scope=scope,
            locale=locale,
            period_start=start,
            period_end=end,
            source_data_version=source_data_version,
            source_versions_json=versions,
            detector_version=self.detector_version,
            prompt_version=STORY_VERBALIZATION_PROMPT_VERSION,
            model_version=settings.OPENROUTER_TEXT_MODEL,
            status="fresh",
            insights_json=ordered_stories,
            ranking_metadata_json=metadata,
        )
        self.db.add(snapshot)
        if previous is not None:
            previous.status = "archived"
        await self.db.flush()
        logger.info("event=snapshot_created user_id=%s scope=%s snapshot_id=%s", user.id, scope, snapshot.snapshot_id)
        return self._response(snapshot)

    async def _record_failed_snapshot(
        self,
        user_id: int,
        scope: str,
        locale: str,
        start: dt.date,
        end: dt.date,
        versions: dict[str, int],
        exc: Exception,
    ) -> None:
        now = dt.datetime.now(dt.UTC)
        failure_key = self._hash([user_id, scope, locale, versions, now.isoformat(), "failed"])
        self.db.add(
            InsightSnapshot(
                snapshot_id=failure_key,
                generation_key=failure_key,
                user_id=user_id,
                insight_scope=scope,
                locale=locale,
                period_start=start,
                period_end=end,
                source_data_version=self._hash(versions),
                source_versions_json=versions,
                detector_version=self.detector_version,
                prompt_version=STORY_VERBALIZATION_PROMPT_VERSION,
                model_version=settings.OPENROUTER_TEXT_MODEL,
                status="failed",
                insights_json=[],
                ranking_metadata_json={"detector_dependencies": self.detector_dependencies},
                error_code=type(exc).__name__,
            )
        )
        await self.db.flush()

    async def story_evidence(self, user_id: int, story_id: str) -> list[dict[str, str]] | None:
        result = await self.db.execute(
            select(InsightSnapshot)
            .where(InsightSnapshot.user_id == user_id)
            .order_by(desc(InsightSnapshot.created_at), desc(InsightSnapshot.id))
        )
        for snapshot in result.scalars().all():
            for story in snapshot.insights_json:
                if story.get("story_id") == story_id:
                    return story.get("evidence", [])
        return None
