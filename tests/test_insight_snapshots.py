import asyncio
import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.providers.openrouter import OpenRouterProvider
from app.insights.comparison import PatternChange, PatternComparer
from app.insights.patterns import VerifiedPattern
from app.insights.snapshot_service import InsightSnapshotService
from app.insights.versioning import DomainEvent, InsightVersionService
from app.models.daily_summary import DailySummary
from app.models.insight import DetectedPattern, InsightSnapshot
from app.models.user import User
from app.schemas.insights import InsightStory

TODAY = dt.date(2026, 7, 20)


class FakeNarrator:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.delay = False

    async def verbalize_insight_stories(self, pattern_inputs: list[dict], *, locale: str = "en"):
        self.calls += 1
        self.started.set()
        if self.delay:
            await self.release.wait()
        if self.fail:
            raise ValueError("invalid narrator response")
        return [
            InsightStory(
                story_id=item["story_id"],
                detector_id=item["detector_id"],
                pattern_key=item["pattern_key"],
                title=f"Verified pattern {index + 1}",
                message="This observation uses verified data only.",
                confidence_label=item["confidence_label"],
                metric=item["metric"],
                explanation="Deterministic supporting metrics are shown below.",
                evidence=item["evidence"],
                category=item["category"],
                direction=item["direction"],
            )
            for index, item in enumerate(pattern_inputs)
        ]


async def _user_with_summaries(
    db: AsyncSession,
    *,
    start: dt.date = TODAY - dt.timedelta(days=6),
    days: int = 7,
) -> User:
    token = uuid4().hex
    user = User(
        firebase_uid=f"insight-{token}",
        email=f"insight-{token}@example.com",
        daily_calorie_goal=2000,
        is_premium=True,
    )
    db.add(user)
    await db.flush()
    for offset in range(days):
        calories = 1900 if offset % 2 == 0 else 2100
        db.add(
            DailySummary(
                user_id=user.id,
                date=start + dt.timedelta(days=offset),
                consumed_calories=calories,
                burned_calories=0,
                remaining_calories=2000 - calories,
                water_glasses=5,
            )
        )
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_unchanged_data_and_elapsed_time_return_identical_snapshot(db_session: AsyncSession) -> None:
    user = await _user_with_summaries(db_session)
    narrator = FakeNarrator()
    service = InsightSnapshotService(db_session, provider=narrator, debounce_seconds=0)

    first = await service.get_stories(user, today=TODAY)
    snapshot = await db_session.scalar(select(InsightSnapshot).where(InsightSnapshot.snapshot_id == first.snapshot_id))
    snapshot.generated_at -= dt.timedelta(hours=1)
    await db_session.flush()
    second = await service.get_stories(user, today=TODAY)

    assert first.snapshot_id == second.snapshot_id
    assert first.stories == second.stories
    assert narrator.calls == 1


@pytest.mark.asyncio
async def test_domain_event_invalidates_only_dependent_patterns(db_session: AsyncSession) -> None:
    user = await _user_with_summaries(db_session)
    narrator = FakeNarrator()
    service = InsightSnapshotService(db_session, provider=narrator, debounce_seconds=0)
    first = await service.get_stories(user, today=TODAY)

    await InsightVersionService(db_session).record(user.id, DomainEvent.WATER_LOGGED, affected_date=TODAY)
    rows = list(
        (
            await db_session.scalars(
                select(DetectedPattern).where(
                    DetectedPattern.user_id == user.id,
                    DetectedPattern.superseded_at.is_(None),
                )
            )
        ).all()
    )

    assert next(row for row in rows if row.detector_id == "hydration").stale_at is not None
    assert next(row for row in rows if row.detector_id == "goal_consistency").stale_at is None

    # No metric changed: a new versioned snapshot reuses the exact story copy.
    second = await service.get_stories(user, today=TODAY)
    assert second.snapshot_id != first.snapshot_id
    assert [story.model_dump() for story in second.stories] == [story.model_dump() for story in first.stories]
    assert narrator.calls == 1


@pytest.mark.asyncio
async def test_unrelated_profile_event_keeps_snapshot_fresh(db_session: AsyncSession) -> None:
    user = await _user_with_summaries(db_session)
    narrator = FakeNarrator()
    service = InsightSnapshotService(db_session, provider=narrator, debounce_seconds=0)
    first = await service.get_stories(user, today=TODAY)

    await InsightVersionService(db_session).record(user.id, DomainEvent.PROFILE_CHANGED)
    second = await service.get_stories(user, today=TODAY)

    assert second.snapshot_id == first.snapshot_id
    assert narrator.calls == 1


@pytest.mark.asyncio
async def test_repeated_events_are_debounced_without_ttl_validity(db_session: AsyncSession) -> None:
    user = await _user_with_summaries(db_session)
    narrator = FakeNarrator()
    service = InsightSnapshotService(db_session, provider=narrator, debounce_seconds=600)
    first = await service.get_stories(user, today=TODAY)

    await InsightVersionService(db_session).record(user.id, DomainEvent.WATER_LOGGED, affected_date=TODAY)
    pending = await service.get_stories(user, today=TODAY)

    assert pending.snapshot_id == first.snapshot_id
    assert pending.update_pending is True
    assert narrator.calls == 1

    stale_snapshot = await db_session.scalar(
        select(InsightSnapshot).where(InsightSnapshot.snapshot_id == first.snapshot_id)
    )
    stale_snapshot.stale_at -= dt.timedelta(seconds=601)
    await db_session.flush()
    refreshed = await service.get_stories(user, today=TODAY)

    assert refreshed.snapshot_id != first.snapshot_id
    # Debounce expiry permits deterministic recomputation; unchanged facts
    # still reuse copy and incur no new narrator call.
    assert narrator.calls == 1


def test_detector_specific_materiality_and_absolute_points() -> None:
    comparer = PatternComparer()
    previous = {
        "pattern_key": "ai_accuracy_trend",
        "payload_json": {
            "accuracy_rate_within_ten_percent": 0.84,
            "median_absolute_correction_percent": 1.4,
            "direction": "improved",
        },
    }
    minimal = VerifiedPattern(
        id="ai_accuracy_trend",
        category="learning",
        confidence=0.9,
        priority=80,
        payload={
            "accuracy_rate_within_ten_percent": 0.86,
            "median_absolute_correction_percent": 1.8,
            "direction": "improved",
        },
    )
    material = minimal.model_copy(
        update={
            "payload": {
                "accuracy_rate_within_ten_percent": 0.80,
                "median_absolute_correction_percent": 3.8,
                "absolute_percentage_point_change": 2.4,
                "older_average_absolute_correction_percent": 1.4,
                "recent_average_absolute_correction_percent": 3.8,
                "older_confirmed_meals": 5,
                "recent_confirmed_meals": 5,
                "direction": "worsened",
            }
        }
    )

    assert comparer.compare(previous, minimal) == PatternChange.MINIMAL
    assert comparer.compare(previous, material) == PatternChange.MATERIAL
    assert OpenRouterProvider._insight_metric(material, locale="en") == "+2.4 percentage points"
    assert OpenRouterProvider._insight_metric(material, locale="it") == "+2,4 punti percentuali"
    assert "more correction" in OpenRouterProvider._fallback_insight(material, locale="en").title.lower()


@pytest.mark.asyncio
async def test_closed_week_is_stable_but_historical_edit_invalidates(db_session: AsyncSession) -> None:
    period_start = dt.date(2026, 7, 6)
    user = await _user_with_summaries(db_session, start=period_start)
    narrator = FakeNarrator()
    service = InsightSnapshotService(db_session, provider=narrator, debounce_seconds=0)
    first = await service.get_stories(
        user,
        scope="weekly_closed",
        period_start=period_start,
        today=TODAY,
    )

    await InsightVersionService(db_session).record(user.id, DomainEvent.MEAL_CREATED, affected_date=TODAY)
    outside_edit = await service.get_stories(
        user,
        scope="weekly_closed",
        period_start=period_start,
        today=TODAY,
    )
    assert outside_edit.snapshot_id == first.snapshot_id

    await InsightVersionService(db_session).record(
        user.id,
        DomainEvent.MEAL_UPDATED,
        affected_date=period_start + dt.timedelta(days=2),
    )
    historical_edit = await service.get_stories(
        user,
        scope="weekly_closed",
        period_start=period_start,
        today=TODAY,
    )
    assert historical_edit.snapshot_id != first.snapshot_id
    assert historical_edit.stories == first.stories


@pytest.mark.asyncio
async def test_llm_failure_preserves_previous_snapshot(db_session: AsyncSession) -> None:
    user = await _user_with_summaries(db_session)
    narrator = FakeNarrator()
    service = InsightSnapshotService(db_session, provider=narrator, debounce_seconds=0)
    first = await service.get_stories(user, today=TODAY)

    summary = await db_session.scalar(
        select(DailySummary).where(DailySummary.user_id == user.id, DailySummary.date == TODAY)
    )
    summary.water_glasses = 12
    await InsightVersionService(db_session).record(user.id, DomainEvent.WATER_LOGGED, affected_date=TODAY)
    narrator.fail = True
    fallback = await service.get_stories(user, today=TODAY)

    assert fallback.snapshot_id == first.snapshot_id
    assert fallback.update_pending is True
    assert fallback.stories == first.stories
    failed = await db_session.scalar(
        select(InsightSnapshot).where(InsightSnapshot.user_id == user.id, InsightSnapshot.status == "failed")
    )
    assert failed is not None


@pytest.mark.asyncio
async def test_no_data_returns_persisted_empty_snapshot_without_llm(db_session: AsyncSession) -> None:
    token = uuid4().hex
    user = User(firebase_uid=token, email=f"{token}@example.com", daily_calorie_goal=2000)
    db_session.add(user)
    await db_session.flush()
    narrator = FakeNarrator()

    result = await InsightSnapshotService(db_session, provider=narrator, debounce_seconds=0).get_stories(
        user, today=TODAY
    )

    assert result.status == "fresh"
    assert result.stories == []
    assert narrator.calls == 0


@pytest.mark.asyncio
async def test_malformed_llm_json_is_rejected_after_one_repair(monkeypatch) -> None:
    provider = OpenRouterProvider()
    calls = 0

    async def malformed(**kwargs):
        nonlocal calls
        calls += 1
        return "not-json", 1, {}

    monkeypatch.setattr(provider, "_post_openrouter", malformed)
    pattern_input = {
        "story_id": "1234567890abcdef",
        "detector_id": "hydration",
        "pattern_key": "hydration_consistency",
        "confidence_label": "high",
        "metric": "5 glasses",
        "evidence": [{"label": "Days", "value": "7"}],
        "category": "water",
        "direction": "neutral",
        "payload": {"id": "hydration_consistency", "payload": {"average_glasses": 5}},
    }

    with pytest.raises(ValueError, match="after repair"):
        await provider.verbalize_insight_stories([pattern_input])
    assert calls == 2


@pytest.mark.asyncio
async def test_concurrent_open_does_not_double_generate(db_session: AsyncSession, test_engine) -> None:
    user = await _user_with_summaries(db_session)
    narrator = FakeNarrator()
    initial = await InsightSnapshotService(db_session, provider=narrator, debounce_seconds=0).get_stories(
        user, today=TODAY
    )
    await db_session.commit()

    Session = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)
    async with Session() as first_db, Session() as second_db:
        first_user = await first_db.get(User, user.id)
        second_user = await second_db.get(User, user.id)
        summary = await first_db.scalar(
            select(DailySummary).where(DailySummary.user_id == user.id, DailySummary.date == TODAY)
        )
        summary.water_glasses = 12
        await InsightVersionService(first_db).record(user.id, DomainEvent.WATER_LOGGED, affected_date=TODAY)
        await first_db.commit()

        narrator.delay = True
        narrator.started.clear()
        generating = asyncio.create_task(
            InsightSnapshotService(first_db, provider=narrator, debounce_seconds=0).get_stories(first_user, today=TODAY)
        )
        await narrator.started.wait()
        pending = await InsightSnapshotService(second_db, provider=narrator, debounce_seconds=0).get_stories(
            second_user, today=TODAY
        )
        narrator.release.set()
        completed = await generating

        assert pending.snapshot_id == initial.snapshot_id
        assert pending.update_pending is True
        assert completed.snapshot_id != initial.snapshot_id
        assert narrator.calls == 2
