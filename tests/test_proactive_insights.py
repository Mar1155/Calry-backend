import datetime as dt
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.insights.versioning import DomainEvent, InsightVersionService
from app.models.daily_summary import DailySummary
from app.models.insight import (
    InsightNotificationDelivery,
    InsightNotificationPreference,
    ProactiveInsight,
    ProactiveInsightEvent,
)
from app.models.user import User
from app.proactive_insights.candidates import CandidateFactory, InsightCandidate, ProactiveTrigger
from app.proactive_insights.notifications import (
    InsightNotificationService,
    PushDeliveryError,
    quiet_hours_end,
)
from app.proactive_insights.quality import InsightQualityGate, QualityGateError
from app.proactive_insights.service import ProactiveInsightService
from app.proactive_insights.verbalizer import GeneratedInsight

TODAY = dt.date(2026, 8, 11)
NOW = dt.datetime(2026, 8, 11, 12, tzinfo=dt.UTC)


class FakeVerbalizer:
    def __init__(self, *, invalid_number: bool = False):
        self.calls = 0
        self.invalid_number = invalid_number

    async def verbalize(self, candidate: InsightCandidate, *, locale: str = "en") -> GeneratedInsight:
        self.calls += 1
        metric_key = next(iter(candidate.metrics))
        label = candidate.type.replace("_", " ")
        body = f"Verified logs show your {label}."
        if self.invalid_number:
            body = "Verified logs show 999 unrecorded entries."
        return GeneratedInsight(
            candidate_id=candidate.candidate_id,
            direction=candidate.direction,
            title=f"{label.title()} noted",
            body=body,
            evidence_refs=[f"metrics.{metric_key}"],
        )


async def _user(db: AsyncSession) -> User:
    token = uuid4().hex
    user = User(
        firebase_uid=f"proactive-{token}",
        email=f"proactive-{token}@example.com",
        daily_calorie_goal=2000,
        is_premium=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _logged_week(db: AsyncSession, user: User) -> None:
    for offset in range(7):
        date = TODAY - dt.timedelta(days=offset)
        db.add(
            DailySummary(
                user_id=user.id,
                date=date,
                consumed_calories=1900,
                burned_calories=0,
                remaining_calories=100,
                water_glasses=6,
            )
        )
    await db.flush()


def _persisted_insight(
    user_id: int,
    *,
    suffix: str = "primary",
    dedup_key: str = "notification-pattern-key-000000000000",
    created_at: dt.datetime = NOW,
) -> ProactiveInsight:
    return ProactiveInsight(
        id=f"insight_notification_{suffix}",
        candidate_id=f"candidate_notification_{suffix}",
        user_id=user_id,
        type="logging_consistency",
        category="logging",
        trigger=ProactiveTrigger.LOGGING_CHANGE.value,
        title="Your journal rhythm is settling",
        body="Calry noticed a steadier rhythm in your recent journal entries.",
        evidence_json={
            "metrics": {"days_logged": 7},
            "evidence": {"source_trigger": ProactiveTrigger.DAILY.value},
        },
        confidence=0.9,
        novelty=0.9,
        relevance=0.9,
        significance=0.8,
        direction="positive",
        dedup_key=dedup_key,
        notification_score=0.9,
        notification_status="ready",
        notification_ready_at=created_at,
        model_version="test-small-model",
        prompt_version="test-prompt-v1",
        created_at=created_at,
    )


class FakePushGateway:
    def __init__(self, failures: list[PushDeliveryError] | None = None):
        self.failures = failures or []
        self.calls = 0

    async def send(self, **kwargs) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return f"fcm-message-{self.calls}"


def _candidate(user_id: int, **updates) -> InsightCandidate:
    values = {
        "candidate_id": "candidate_0123456789abcdef0123456789abcdef",
        "type": "ai_accuracy_trend",
        "category": "learning",
        "trigger": ProactiveTrigger.AI_ACCURACY_CHANGE.value,
        "user_id": user_id,
        "evidence": {"period_end": TODAY.isoformat()},
        "metrics": {"confirmed_meals": 8, "direction": "improved"},
        "confidence": 0.9,
        "novelty": 0.9,
        "relevance": 0.9,
        "significance": 0.8,
        "usefulness": 0.8,
        "urgency": 0.3,
        "interruption_cost": 0.3,
        "direction": "positive",
        "dedup_key": "0123456789abcdef0123456789abcdef",
        "created_at": NOW,
    }
    values.update(updates)
    return InsightCandidate(**values)


@pytest.mark.asyncio
async def test_event_inbox_is_idempotent(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    kwargs = {
        "user_id": user.id,
        "trigger": DomainEvent.MEAL_CREATED.value,
        "affected_date": TODAY,
        "source_versions": {"meal_data_version": 1},
    }

    first = await ProactiveInsightService.stage_event(db_session, **kwargs)
    second = await ProactiveInsightService.stage_event(db_session, **kwargs)

    assert first.event_id == second.event_id
    assert (
        await db_session.scalar(
            select(func.count(ProactiveInsightEvent.id)).where(
                ProactiveInsightEvent.event_id == first.event_id
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_domain_version_event_stages_transactional_proactive_event(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)

    versions = await InsightVersionService(db_session).record(
        user.id, DomainEvent.MEAL_CREATED, affected_date=TODAY
    )
    event = await db_session.scalar(
        select(ProactiveInsightEvent).where(
            ProactiveInsightEvent.user_id == user.id,
            ProactiveInsightEvent.trigger == DomainEvent.MEAL_CREATED.value,
        )
    )

    assert event is not None
    assert event.status == "pending"
    assert event.source_versions_json == versions


@pytest.mark.asyncio
async def test_meal_event_creates_verified_calorie_milestone_candidate(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    db_session.add(
        DailySummary(
            user_id=user.id,
            date=TODAY,
            consumed_calories=1540,
            burned_calories=0,
            remaining_calories=460,
            water_glasses=0,
        )
    )
    await db_session.flush()
    event = await ProactiveInsightService.stage_event(
        db_session,
        user_id=user.id,
        trigger=DomainEvent.MEAL_CREATED.value,
        affected_date=TODAY,
        source_versions={"meal_data_version": 1},
    )

    candidates = await ProactiveInsightService(db_session, verbalizer=FakeVerbalizer()).candidates_for(
        event, user, today=TODAY, now=NOW
    )
    milestone = next(item for item in candidates if item.trigger == ProactiveTrigger.CALORIE_MILESTONE.value)

    assert milestone.metrics["milestone"] == "75_percent"
    assert milestone.metrics["consumed_calories"] == 1540
    assert milestone.confidence == 1.0


@pytest.mark.asyncio
async def test_valid_candidate_persists_once_and_event_retry_is_safe(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    await _logged_week(db_session, user)
    event = await ProactiveInsightService.stage_event(
        db_session,
        user_id=user.id,
        trigger=ProactiveTrigger.DAILY.value,
        affected_date=TODAY,
        source_versions={},
        discriminator=TODAY.isoformat(),
    )
    verbalizer = FakeVerbalizer()
    service = ProactiveInsightService(db_session, verbalizer=verbalizer)

    first = await service.process_event(event.event_id, today=TODAY, now=NOW)
    first_count = int(await db_session.scalar(select(func.count(ProactiveInsight.id))) or 0)
    first_calls = verbalizer.calls
    second = await service.process_event(event.event_id, today=TODAY, now=NOW)

    assert first["status"] == "completed"
    assert first_count >= 1
    assert second == {"status": "completed", "persisted": first_count}
    assert await db_session.scalar(select(func.count(ProactiveInsight.id))) == first_count
    assert verbalizer.calls == first_calls
    rows = list((await db_session.scalars(select(ProactiveInsight))).all())
    assert all(row.evidence_json["metrics"] for row in rows)
    assert all(row.model_version and row.prompt_version for row in rows)


@pytest.mark.asyncio
async def test_invalid_llm_copy_is_rejected_not_persisted(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    event = await ProactiveInsightService.stage_event(
        db_session,
        user_id=user.id,
        trigger=ProactiveTrigger.DAILY.value,
        affected_date=TODAY,
        source_versions={},
        discriminator="invalid-copy",
    )
    service = ProactiveInsightService(db_session, verbalizer=FakeVerbalizer(invalid_number=True))
    candidate = _candidate(user.id)

    async def one_candidate(*args, **kwargs):
        return [candidate]

    service.candidates_for = one_candidate
    result = await service.process_event(event.event_id, today=TODAY, now=NOW)

    assert result == {"status": "completed", "persisted": 0}
    assert await db_session.scalar(select(func.count(ProactiveInsight.id))) == 0
    assert event.result_json["rejected"] == 1


def test_quality_gate_rejects_unverified_numbers_direction_and_support() -> None:
    candidate = _candidate(1)
    gate = InsightQualityGate()

    with pytest.raises(QualityGateError, match="unverified_number"):
        gate.validate(
            candidate,
            title="Accuracy update",
            body="Accuracy improved across 99 meals.",
            evidence_refs=["metrics.confirmed_meals"],
        )
    with pytest.raises(QualityGateError, match="reversed_direction"):
        gate.validate(
            candidate,
            title="Accuracy update",
            body="Accuracy worsened across 8 meals.",
            evidence_refs=["metrics.confirmed_meals"],
        )
    with pytest.raises(QualityGateError, match="unsupported_claim"):
        gate.validate(
            candidate,
            title="Accuracy update",
            body="Accuracy improved across 8 meals.",
            evidence_refs=["metrics.sleep_quality"],
        )


@pytest.mark.asyncio
async def test_threshold_gate_stops_low_value_candidate_before_llm(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    event = await ProactiveInsightService.stage_event(
        db_session,
        user_id=user.id,
        trigger=ProactiveTrigger.DAILY.value,
        affected_date=TODAY,
        source_versions={},
        discriminator="low-value",
    )
    verbalizer = FakeVerbalizer()
    service = ProactiveInsightService(db_session, verbalizer=verbalizer)
    candidate = _candidate(user.id, confidence=0.4)

    async def one_candidate(*args, **kwargs):
        return [candidate]

    service.candidates_for = one_candidate
    result = await service.process_event(event.event_id, today=TODAY, now=NOW)

    assert result == {"status": "completed", "persisted": 0}
    assert verbalizer.calls == 0
    assert event.result_json["ineligible"] == 1


@pytest.mark.asyncio
async def test_missing_push_token_does_not_block_insight_persistence(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    event = await ProactiveInsightService.stage_event(
        db_session,
        user_id=user.id,
        trigger=ProactiveTrigger.DAILY.value,
        affected_date=TODAY,
        source_versions={},
        discriminator="missing-push-token",
    )
    candidate = _candidate(user.id)
    service = ProactiveInsightService(db_session, verbalizer=FakeVerbalizer())

    async def one_candidate(*args, **kwargs):
        return [candidate]

    service.candidates_for = one_candidate
    result = await service.process_event(event.event_id, today=TODAY, now=NOW)
    insight = await db_session.scalar(
        select(ProactiveInsight).where(ProactiveInsight.user_id == user.id)
    )

    assert result == {"status": "completed", "persisted": 1}
    assert insight is not None
    assert insight.notification_status == "suppressed"
    assert insight.notification_ready_at.replace(tzinfo=dt.UTC) == NOW


def test_repeated_meal_requires_multiple_days() -> None:
    meals = [
        type("Meal", (), {"meal_name": "Greek yogurt", "created_at": NOW - dt.timedelta(days=offset)})
        for offset in (0, 1, 2)
    ]
    candidate = CandidateFactory.repeated_meal(
        user_id=1,
        source_trigger=DomainEvent.MEAL_CREATED.value,
        meals=meals,
        period_start=TODAY - dt.timedelta(days=6),
        period_end=TODAY,
        now=NOW,
    )

    assert candidate is not None
    assert candidate.metrics == {
        "meal_name": "Greek yogurt",
        "times_logged": 3,
        "distinct_days": 3,
        "period_days": 7,
    }


@pytest.mark.asyncio
async def test_diary_api_lists_and_marks_owned_insight_read(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = {"Authorization": "Bearer mock_token_proactive_diary_test"}
    profile = await client.get("/api/v1/users/me", headers=headers)
    user_id = profile.json()["id"]
    await client.post(
        "/api/v1/premium/sync",
        json={
            "is_premium": True,
            "entitlement": "Calry Pro",
            "expires_at": "2030-01-01T00:00:00Z",
            "revenuecat_app_user_id": "proactive_diary_test",
        },
        headers=headers,
    )
    insight = ProactiveInsight(
        id="insight_0123456789abcdef0123456789abcdef",
        candidate_id="candidate_abcdef0123456789abcdef0123456789",
        user_id=user_id,
        type="logging_consistency",
        category="logging",
        trigger=ProactiveTrigger.LOGGING_CHANGE.value,
        title="Logging pattern noted",
        body="Your recent logs show a consistent pattern.",
        evidence_json={"metrics": {"days_logged": 7}, "evidence": {}},
        confidence=0.9,
        novelty=0.8,
        relevance=0.8,
        significance=0.7,
        direction="neutral",
        dedup_key="abcdef0123456789abcdef0123456789",
        notification_score=0.5,
        notification_status="not_eligible",
        model_version="test-small-model",
        prompt_version="test-prompt-v1",
        created_at=NOW,
    )
    db_session.add(insight)
    await db_session.commit()

    diary = await client.get("/api/v1/insights/diary", headers=headers)
    unread_before = await client.get("/api/v1/insights/diary/unread", headers=headers)
    detail = await client.get(f"/api/v1/insights/diary/{insight.id}", headers=headers)
    marked = await client.patch(f"/api/v1/insights/diary/{insight.id}/read", headers=headers)
    unread_after = await client.get("/api/v1/insights/diary/unread", headers=headers)

    assert diary.status_code == 200
    assert diary.json()[0]["id"] == insight.id
    assert diary.json()[0]["read_at"] is None
    assert unread_before.json() == {"unread_count": 1}
    assert detail.json()["id"] == insight.id
    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None
    assert unread_after.json() == {"unread_count": 0}


@pytest.mark.asyncio
async def test_stronger_evidence_links_and_supersedes_without_deleting_history(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    earlier = _persisted_insight(
        user.id,
        suffix="earlier-related",
        dedup_key="evolving-pattern-dedup-key-00000000",
        created_at=NOW - dt.timedelta(days=8),
    )
    earlier.type = "ai_accuracy_trend"
    earlier.category = "learning"
    earlier.significance = 0.5
    earlier.title = "An early pattern appeared"
    earlier.body = "Calry found the first outline of a pattern in confirmed estimates."
    earlier.notification_status = "not_eligible"
    db_session.add(earlier)
    await db_session.flush()
    event = await ProactiveInsightService.stage_event(
        db_session,
        user_id=user.id,
        trigger=ProactiveTrigger.DAILY.value,
        affected_date=TODAY,
        source_versions={},
        discriminator="related-pattern-growth",
    )
    candidate = _candidate(
        user.id,
        candidate_id="candidate_related_growth_0123456789abcdef",
        dedup_key=earlier.dedup_key,
        significance=0.8,
    )
    service = ProactiveInsightService(db_session, verbalizer=FakeVerbalizer())

    async def one_candidate(*args, **kwargs):
        return [candidate]

    service.candidates_for = one_candidate
    result = await service.process_event(event.event_id, today=TODAY, now=NOW)
    rows = list(
        (
            await db_session.scalars(
                select(ProactiveInsight)
                .where(ProactiveInsight.user_id == user.id)
                .order_by(ProactiveInsight.created_at)
            )
        ).all()
    )

    assert result == {"status": "completed", "persisted": 1}
    assert len(rows) == 2
    assert rows[1].related_insight_id == earlier.id
    assert earlier.superseded_at == NOW
    assert earlier in rows


@pytest.mark.asyncio
async def test_notification_preferences_are_account_owned_and_validated(
    client: AsyncClient,
) -> None:
    headers = {"Authorization": "Bearer mock_token_insight_preferences"}
    await client.get("/api/v1/users/me", headers=headers)

    defaults = await client.get("/api/v1/insights/preferences", headers=headers)
    updated = await client.patch(
        "/api/v1/insights/preferences",
        json={
            "proactive_enabled": True,
            "daily_enabled": False,
            "weekly_enabled": True,
            "quiet_hours_start": "22:30",
            "quiet_hours_end": "07:15",
            "timezone": "Europe/Rome",
        },
        headers=headers,
    )
    invalid = await client.patch(
        "/api/v1/insights/preferences",
        json={"timezone": "Not/A_Timezone"},
        headers=headers,
    )

    assert defaults.status_code == 200
    assert defaults.json()["proactive_enabled"] is False
    assert updated.status_code == 200
    assert updated.json() == {
        "proactive_enabled": True,
        "daily_enabled": False,
        "weekly_enabled": True,
        "quiet_hours_start": "22:30",
        "quiet_hours_end": "07:15",
        "timezone": "Europe/Rome",
    }
    assert invalid.status_code == 422


def test_quiet_hours_are_timezone_aware() -> None:
    # 20:30 UTC is 22:30 in Rome in August, inside 21:00–08:00 quiet hours.
    delayed = quiet_hours_end(
        dt.datetime(2026, 8, 11, 20, 30, tzinfo=dt.UTC),
        timezone="Europe/Rome",
        start="21:00",
        end="08:00",
    )

    assert delayed == dt.datetime(2026, 8, 12, 6, 0, tzinfo=dt.UTC)
    assert (
        quiet_hours_end(
            dt.datetime(2026, 8, 11, 10, tzinfo=dt.UTC),
            timezone="Europe/Rome",
            start="21:00",
            end="08:00",
        )
        is None
    )


@pytest.mark.asyncio
async def test_periodic_evaluation_uses_each_users_local_time(
    db_session: AsyncSession,
) -> None:
    rome_user = await _user(db_session)
    los_angeles_user = await _user(db_session)
    db_session.add_all(
        [
            InsightNotificationPreference(
                user_id=rome_user.id,
                timezone="Europe/Rome",
            ),
            InsightNotificationPreference(
                user_id=los_angeles_user.id,
                timezone="America/Los_Angeles",
            ),
        ]
    )
    await db_session.flush()

    event_ids = await ProactiveInsightService(db_session).stage_due_periodic(
        now=dt.datetime(2026, 8, 11, 18, 5, tzinfo=dt.UTC)
    )
    events = list(
        (
            await db_session.scalars(
                select(ProactiveInsightEvent).where(
                    ProactiveInsightEvent.event_id.in_(event_ids)
                )
            )
        ).all()
    )

    events_by_user = {event.user_id: event for event in events}
    assert rome_user.id in events_by_user
    assert los_angeles_user.id not in events_by_user
    assert events_by_user[rome_user.id].trigger == ProactiveTrigger.DAILY.value
    assert (
        events_by_user[rome_user.id].payload_json["evaluation_timezone"]
        == "Europe/Rome"
    )


@pytest.mark.asyncio
async def test_disabled_notifications_preserve_diary_entry(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    user.fcm_token = "test-device-token-long-enough"
    insight = _persisted_insight(user.id, suffix="disabled")
    db_session.add_all(
        [
            insight,
            InsightNotificationPreference(
                user_id=user.id,
                proactive_enabled=False,
                timezone="Europe/Rome",
            ),
        ]
    )
    await db_session.flush()

    delivery = await InsightNotificationService(db_session).schedule(
        insight, user, now=NOW
    )

    assert await db_session.get(ProactiveInsight, insight.id) is insight
    assert delivery is not None
    assert delivery.status == "suppressed"
    assert delivery.suppression_reason == "user_disabled"
    assert insight.notification_sent_at is None


@pytest.mark.asyncio
async def test_quiet_hours_delay_existing_insight_without_regeneration(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    user.fcm_token = "test-device-token-long-enough"
    now = dt.datetime(2026, 8, 11, 20, 30, tzinfo=dt.UTC)
    insight = _persisted_insight(user.id, suffix="quiet", created_at=now)
    db_session.add_all(
        [
            insight,
            InsightNotificationPreference(
                user_id=user.id,
                proactive_enabled=True,
                timezone="Europe/Rome",
                quiet_hours_start="21:00",
                quiet_hours_end="08:00",
            ),
        ]
    )
    await db_session.flush()

    delivery = await InsightNotificationService(db_session).schedule(
        insight, user, now=now
    )

    assert delivery is not None
    assert delivery.status == "scheduled"
    assert delivery.scheduled_for == dt.datetime(2026, 8, 12, 6, tzinfo=dt.UTC)
    assert delivery.insight_id == insight.id
    assert insight.notification_status == "scheduled"


@pytest.mark.asyncio
async def test_push_delivery_is_idempotent_and_persists_provider_metadata(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    user.fcm_token = "test-device-token-long-enough"
    insight = _persisted_insight(user.id, suffix="success")
    preference = InsightNotificationPreference(
        user_id=user.id,
        proactive_enabled=True,
        timezone="UTC",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
    )
    db_session.add_all([insight, preference])
    await db_session.flush()
    gateway = FakePushGateway()
    service = InsightNotificationService(db_session, gateway=gateway)
    delivery = await service.schedule(insight, user, now=NOW)
    await db_session.commit()

    first = await service.deliver(delivery.id, now=NOW)
    second = await service.deliver(delivery.id, now=NOW)
    stored = await db_session.get(InsightNotificationDelivery, delivery.id)

    assert first == {"status": "sent"}
    assert second == {"status": "sent"}
    assert gateway.calls == 1
    assert stored.provider_message_id == "fcm-message-1"
    assert stored.idempotency_key
    assert insight.notification_sent_at == NOW


@pytest.mark.asyncio
async def test_transient_push_failure_retries_without_losing_insight(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    user.fcm_token = "test-device-token-long-enough"
    insight = _persisted_insight(user.id, suffix="retry")
    db_session.add_all(
        [
            insight,
            InsightNotificationPreference(
                user_id=user.id,
                proactive_enabled=True,
                timezone="UTC",
                quiet_hours_start="21:00",
                quiet_hours_end="08:00",
            ),
        ]
    )
    await db_session.flush()
    gateway = FakePushGateway(
        [PushDeliveryError("UnavailableError", transient=True)]
    )
    service = InsightNotificationService(db_session, gateway=gateway)
    delivery = await service.schedule(insight, user, now=NOW)
    await db_session.commit()

    failed = await service.deliver(delivery.id, now=NOW)
    retried = await service.deliver(
        delivery.id,
        now=NOW + dt.timedelta(seconds=301),
    )

    assert failed == {"status": "failed"}
    assert retried == {"status": "sent"}
    assert gateway.calls == 2
    assert insight.notification_sent_at == NOW + dt.timedelta(seconds=301)
    assert await db_session.get(ProactiveInsight, insight.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suppression", ["daily_limit", "cooldown", "superseded", "stale"]
)
async def test_delivery_policy_suppresses_noise(
    db_session: AsyncSession,
    suppression: str,
) -> None:
    user = await _user(db_session)
    user.fcm_token = "test-device-token-long-enough"
    suffix = f"policy-{suppression}"
    insight = _persisted_insight(user.id, suffix=suffix)
    preference = InsightNotificationPreference(
        user_id=user.id,
        proactive_enabled=True,
        timezone="UTC",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
    )
    db_session.add_all([insight, preference])
    if suppression in {"daily_limit", "cooldown"}:
        prior = _persisted_insight(
            user.id,
            suffix=f"prior-{suppression}",
            dedup_key=(
                insight.dedup_key
                if suppression == "cooldown"
                else "different-notification-pattern-0000"
            ),
            created_at=NOW - dt.timedelta(hours=1),
        )
        prior.notification_status = "sent"
        prior.notification_sent_at = NOW - dt.timedelta(hours=1)
        db_session.add(prior)
        if suppression == "cooldown":
            # Weekly sends are outside the ordinary daily cap, isolating cooldown.
            prior.evidence_json["evidence"]["source_trigger"] = ProactiveTrigger.WEEKLY.value
    elif suppression == "superseded":
        insight.superseded_at = NOW
    else:
        insight.created_at = NOW - dt.timedelta(hours=73)
    await db_session.flush()
    gateway = FakePushGateway()
    service = InsightNotificationService(db_session, gateway=gateway)
    delivery = await service.schedule(insight, user, now=NOW)
    await db_session.commit()

    result = await service.deliver(delivery.id, now=NOW)

    assert result == {"status": "suppressed"}
    assert delivery.suppression_reason == suppression
    assert gateway.calls == 0
    assert await db_session.get(ProactiveInsight, insight.id) is not None


@pytest.mark.asyncio
async def test_permanent_push_failure_keeps_diary_and_clears_invalid_token(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    user.fcm_token = "test-device-token-long-enough"
    insight = _persisted_insight(user.id, suffix="permanent-failure")
    db_session.add_all(
        [
            insight,
            InsightNotificationPreference(
                user_id=user.id,
                proactive_enabled=True,
                timezone="UTC",
                quiet_hours_start="21:00",
                quiet_hours_end="08:00",
            ),
        ]
    )
    await db_session.flush()
    service = InsightNotificationService(
        db_session,
        gateway=FakePushGateway(
            [
                PushDeliveryError(
                    "UnregisteredError",
                    transient=False,
                    invalidate_token=True,
                )
            ]
        ),
    )
    delivery = await service.schedule(insight, user, now=NOW)
    await db_session.commit()

    result = await service.deliver(delivery.id, now=NOW)

    assert result == {"status": "suppressed"}
    assert delivery.suppression_reason == "permanent_failure"
    assert user.fcm_token is None
    assert insight.notification_sent_at is None
    assert await db_session.get(ProactiveInsight, insight.id) is not None
