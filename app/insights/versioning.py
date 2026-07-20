import datetime as dt
import logging
from enum import StrEnum

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.insights.detectors import PatternDetector
from app.models.insight import DetectedPattern, InsightSnapshot, UserInsightVersion

logger = logging.getLogger("app.insights.versioning")


class DomainVersion(StrEnum):
    MEAL = "meal_data_version"
    ACTIVITY = "activity_data_version"
    HYDRATION = "hydration_data_version"
    PROFILE = "profile_data_version"
    TARGET = "target_data_version"
    WEIGHT = "weight_data_version"
    AI_ACCURACY = "ai_accuracy_data_version"
    LOGGING = "logging_behavior_version"


class DomainEvent(StrEnum):
    MEAL_CREATED = "MealCreated"
    MEAL_UPDATED = "MealUpdated"
    MEAL_DELETED = "MealDeleted"
    MEAL_CORRECTED = "MealCorrected"
    MEAL_CATEGORY_CHANGED = "MealCategoryChanged"
    ACTIVITY_LOGGED = "ActivityLogged"
    ACTIVITY_DELETED = "ActivityDeleted"
    WATER_LOGGED = "WaterLogged"
    WATER_REMOVED = "WaterRemoved"
    TARGET_CHANGED = "TargetChanged"
    WEIGHT_UPDATED = "WeightUpdated"
    PROFILE_CHANGED = "ProfileChanged"


EVENT_DOMAINS: dict[DomainEvent, frozenset[DomainVersion]] = {
    DomainEvent.MEAL_CREATED: frozenset({DomainVersion.MEAL, DomainVersion.LOGGING}),
    DomainEvent.MEAL_UPDATED: frozenset({DomainVersion.MEAL}),
    DomainEvent.MEAL_DELETED: frozenset({DomainVersion.MEAL, DomainVersion.LOGGING}),
    DomainEvent.MEAL_CORRECTED: frozenset({DomainVersion.MEAL, DomainVersion.AI_ACCURACY}),
    DomainEvent.MEAL_CATEGORY_CHANGED: frozenset({DomainVersion.MEAL}),
    DomainEvent.ACTIVITY_LOGGED: frozenset({DomainVersion.ACTIVITY}),
    DomainEvent.ACTIVITY_DELETED: frozenset({DomainVersion.ACTIVITY}),
    DomainEvent.WATER_LOGGED: frozenset({DomainVersion.HYDRATION}),
    DomainEvent.WATER_REMOVED: frozenset({DomainVersion.HYDRATION}),
    DomainEvent.TARGET_CHANGED: frozenset({DomainVersion.TARGET}),
    DomainEvent.WEIGHT_UPDATED: frozenset({DomainVersion.WEIGHT}),
    DomainEvent.PROFILE_CHANGED: frozenset({DomainVersion.PROFILE}),
}


class InsightVersionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _advisory_lock(self, user_id: int) -> None:
        bind = self.db.get_bind()
        if bind.dialect.name == "postgresql":
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, :user_id)"),
                {"namespace": 9127, "user_id": user_id},
            )

    async def get_or_create(self, user_id: int, *, lock: bool = False) -> UserInsightVersion:
        if lock:
            await self._advisory_lock(user_id)
        stmt = select(UserInsightVersion).where(UserInsightVersion.user_id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        versions = result.scalar_one_or_none()
        if versions is None:
            versions = UserInsightVersion(user_id=user_id)
            self.db.add(versions)
            await self.db.flush()
        return versions

    async def current(self, user_id: int) -> dict[str, int]:
        return (await self.get_or_create(user_id)).as_dict()

    async def record(
        self,
        user_id: int,
        *events: DomainEvent,
        affected_date: dt.date | None = None,
    ) -> dict[str, int]:
        domains = {domain for event in events for domain in EVENT_DOMAINS[event]}
        if not domains:
            return await self.current(user_id)

        versions = await self.get_or_create(user_id, lock=True)
        for domain in domains:
            setattr(versions, domain.value, getattr(versions, domain.value) + 1)
        versions.updated_at = dt.datetime.now(dt.UTC)
        await self.db.flush()

        detector_ids = {
            detector.detector_id
            for detector in (detector_cls() for detector_cls in PatternDetector.registry)
            if detector.dependencies.intersection(domain.value for domain in domains)
        }
        now = dt.datetime.now(dt.UTC)
        if detector_ids:
            pattern_result = await self.db.execute(
                select(DetectedPattern).where(
                    DetectedPattern.user_id == user_id,
                    DetectedPattern.detector_id.in_(detector_ids),
                    DetectedPattern.superseded_at.is_(None),
                )
            )
            for pattern in pattern_result.scalars().all():
                if (
                    affected_date is None
                    or not pattern.scope.startswith("weekly")
                    or pattern.period_start <= affected_date <= pattern.period_end
                ):
                    pattern.stale_at = now

        snapshot_result = await self.db.execute(
            select(InsightSnapshot).where(
                InsightSnapshot.user_id == user_id,
                InsightSnapshot.status.in_(("fresh", "generating")),
            )
        )
        for snapshot in snapshot_result.scalars().all():
            detector_dependencies = snapshot.ranking_metadata_json.get("detector_dependencies", {})
            affected = any(set(dependencies).intersection(domain.value for domain in domains) for dependencies in detector_dependencies.values())
            if not affected:
                continue
            if affected_date is not None and snapshot.insight_scope.startswith("weekly"):
                if not snapshot.period_start <= affected_date <= snapshot.period_end:
                    continue
            snapshot.status = "stale"
            snapshot.stale_at = now

        await self.db.flush()
        logger.info(
            "event=domain_version_incremented user_id=%s events=%s domains=%s detector_count=%s",
            user_id,
            [event.value for event in events],
            sorted(domain.value for domain in domains),
            len(detector_ids),
        )
        return versions.as_dict()
