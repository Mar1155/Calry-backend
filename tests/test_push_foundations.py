import datetime as dt
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insight import InsightNotificationPreference, NotificationDelivery
from app.models.push_device import PushDevice
from app.models.user import User
from app.notifications.devices import PushDeviceService
from app.notifications.dispatcher import (
    PRIORITY_DIGEST,
    PRIORITY_NORMAL,
    PRIORITY_TRANSACTIONAL,
    PushDispatcher,
)
from app.notifications.gateway import PushDeliveryError

NOW = dt.datetime(2026, 11, 26, 12, tzinfo=dt.UTC)


class RecordingGateway:
    def __init__(self, failures: dict[str, PushDeliveryError] | None = None):
        self.failures = failures or {}
        self.sent: list[dict] = []

    async def send(self, *, token: str, title: str, body: str, data: dict[str, str]) -> str:
        if token in self.failures:
            raise self.failures[token]
        self.sent.append({"token": token, "title": title, "body": body, "data": data})
        return f"fcm-{len(self.sent)}"


async def _user(db: AsyncSession, *, timezone: str | None = None) -> User:
    token = uuid4().hex
    user = User(
        firebase_uid=f"push-{token}",
        email=f"push-{token}@example.com",
        daily_calorie_goal=2000,
        timezone=timezone,
    )
    db.add(user)
    await db.flush()
    return user


async def _enqueue(
    dispatcher: PushDispatcher,
    user: User,
    *,
    key: str,
    category: str = "meal_reminder",
    priority: str = PRIORITY_NORMAL,
    now: dt.datetime = NOW,
) -> NotificationDelivery:
    return await dispatcher.enqueue(
        user,
        notification_type="test_reminder",
        category=category,
        priority=priority,
        title="Calry",
        body="Lunch done? Tell me in a few words.",
        data={"type": "test_reminder", "route": "/"},
        idempotency_key=f"{key}-{uuid4().hex}",
        now=now,
    )


@pytest.mark.asyncio
async def test_delivery_fans_out_to_every_device_and_drops_dead_tokens(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    devices = PushDeviceService(db_session)
    await devices.register(user, "phone-token-0000000001", platform="ios")
    await devices.register(user, "tablet-token-000000002", platform="ios")
    gateway = RecordingGateway(
        {
            "tablet-token-000000002": PushDeliveryError(
                "UnregisteredError", transient=False, invalidate_token=True
            )
        }
    )
    dispatcher = PushDispatcher(db_session, gateway=gateway)
    delivery = await _enqueue(dispatcher, user, key="fanout")
    await db_session.commit()

    result = await dispatcher.deliver(delivery.id, now=NOW)

    assert result == {"status": "sent"}
    assert [item["token"] for item in gateway.sent] == ["phone-token-0000000001"]
    assert gateway.sent[0]["data"]["delivery_id"] == str(delivery.id)
    assert await devices.tokens(user.id) == ["phone-token-0000000001"]


@pytest.mark.asyncio
async def test_token_moves_to_the_account_signed_in_on_that_phone(
    db_session: AsyncSession,
) -> None:
    first = await _user(db_session)
    second = await _user(db_session)
    devices = PushDeviceService(db_session)
    await devices.register(first, "shared-phone-token-0001")

    await devices.register(second, "shared-phone-token-0001")
    await db_session.refresh(first)

    assert await devices.tokens(first.id) == []
    assert first.fcm_token is None
    assert await devices.tokens(second.id) == ["shared-phone-token-0001"]
    assert second.fcm_token == "shared-phone-token-0001"


@pytest.mark.asyncio
async def test_unregister_endpoint_removes_only_this_device(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = {"Authorization": "Bearer mock_token_push_devices"}
    me = await client.get("/api/v1/users/me", headers=headers)
    own = select(PushDevice.token).where(PushDevice.user_id == me.json()["id"])
    for token in ("device-one-token-00001", "device-two-token-00002"):
        response = await client.post(
            "/api/v1/users/me/fcm-token",
            json={"token": token, "platform": "android"},
            headers=headers,
        )
        assert response.status_code == 204

    removed = await client.post(
        "/api/v1/users/me/fcm-token/unregister",
        json={"token": "device-one-token-00001"},
        headers=headers,
    )
    remaining = list((await db_session.scalars(own)).all())
    legacy = await client.delete("/api/v1/users/me/fcm-token", headers=headers)
    after_legacy = list((await db_session.scalars(own)).all())

    assert removed.status_code == 204
    assert remaining == ["device-two-token-00002"]
    assert legacy.status_code == 204
    assert after_legacy == []


@pytest.mark.asyncio
async def test_quiet_hours_follow_the_zone_the_app_reports(db_session: AsyncSession) -> None:
    # 21:30 UTC is 22:30 in Rome: quiet for this person even though the
    # settings row still carries the UTC default.
    now = dt.datetime(2026, 11, 26, 21, 30, tzinfo=dt.UTC)
    user = await _user(db_session, timezone="Europe/Rome")
    db_session.add(InsightNotificationPreference(user_id=user.id, timezone="UTC"))
    await PushDeviceService(db_session).register(user, "rome-phone-token-00001")
    dispatcher = PushDispatcher(db_session, gateway=RecordingGateway())

    delivery = await _enqueue(dispatcher, user, key="quiet", now=now)

    assert delivery.status == "scheduled"
    # Rome is UTC+1 in November: quiet hours end at 08:00 local = 07:00 UTC.
    assert delivery.scheduled_for == dt.datetime(2026, 11, 27, 7, tzinfo=dt.UTC)


@pytest.mark.asyncio
async def test_daily_cap_is_shared_across_types_but_skips_digests_and_transactional(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, timezone="UTC")
    await PushDeviceService(db_session).register(user, "capped-phone-token-001")
    gateway = RecordingGateway()
    dispatcher = PushDispatcher(db_session, gateway=gateway)

    first = await _enqueue(dispatcher, user, key="first")
    second = await _enqueue(dispatcher, user, key="second", category="daily_summary")
    digest = await _enqueue(dispatcher, user, key="digest", priority=PRIORITY_DIGEST)
    receipt = await _enqueue(
        dispatcher, user, key="receipt", category="account", priority=PRIORITY_TRANSACTIONAL
    )
    await db_session.commit()

    assert await dispatcher.deliver(first.id, now=NOW) == {"status": "sent"}
    assert await dispatcher.deliver(second.id, now=NOW) == {"status": "suppressed"}
    assert await dispatcher.deliver(digest.id, now=NOW) == {"status": "sent"}
    assert await dispatcher.deliver(receipt.id, now=NOW) == {"status": "sent"}
    stored = await db_session.get(NotificationDelivery, second.id)
    assert stored.suppression_reason == "daily_limit"
    assert len(gateway.sent) == 3


@pytest.mark.asyncio
async def test_transactional_push_ignores_quiet_hours(db_session: AsyncSession) -> None:
    now = dt.datetime(2026, 11, 26, 23, 30, tzinfo=dt.UTC)
    user = await _user(db_session, timezone="UTC")
    await PushDeviceService(db_session).register(user, "late-phone-token-00001")
    dispatcher = PushDispatcher(db_session, gateway=RecordingGateway())

    delivery = await _enqueue(
        dispatcher,
        user,
        key="late",
        category="account",
        priority=PRIORITY_TRANSACTIONAL,
        now=now,
    )

    assert delivery.status == "scheduled"
    assert delivery.scheduled_for == now


@pytest.mark.asyncio
async def test_weekly_cap_limits_normal_pushes(db_session: AsyncSession) -> None:
    user = await _user(db_session, timezone="UTC")
    await PushDeviceService(db_session).register(user, "weekly-phone-token-001")
    dispatcher = PushDispatcher(db_session, gateway=RecordingGateway())
    results = []
    for day in range(5):
        now = NOW - dt.timedelta(days=4 - day)
        delivery = await _enqueue(dispatcher, user, key=f"week-{day}", now=now)
        await db_session.commit()
        results.append(await dispatcher.deliver(delivery.id, now=now))

    assert results[:4] == [{"status": "sent"}] * 4
    assert results[4] == {"status": "suppressed"}


@pytest.mark.asyncio
async def test_category_opt_out_is_validated_and_respected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    headers = {"Authorization": "Bearer mock_token_push_categories"}
    me = await client.get("/api/v1/users/me", headers=headers)
    rejected = await client.patch(
        "/api/v1/insights/preferences",
        json={"disabled_categories": ["marketing"]},
        headers=headers,
    )
    accepted = await client.patch(
        "/api/v1/insights/preferences",
        json={"disabled_categories": ["meal_reminder", "meal_reminder"]},
        headers=headers,
    )
    user = await db_session.get(User, me.json()["id"])
    await PushDeviceService(db_session).register(user, "opt-out-phone-token-01")
    dispatcher = PushDispatcher(db_session, gateway=RecordingGateway())

    muted = await _enqueue(dispatcher, user, key="muted")
    allowed = await _enqueue(dispatcher, user, key="allowed", category="daily_summary")

    assert rejected.status_code == 422
    assert accepted.json()["disabled_categories"] == ["meal_reminder"]
    assert muted.status == "suppressed"
    assert muted.suppression_reason == "category_disabled"
    assert allowed.status == "scheduled"


@pytest.mark.asyncio
async def test_new_device_revives_a_push_suppressed_for_missing_token(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, timezone="UTC")
    dispatcher = PushDispatcher(db_session, gateway=RecordingGateway())
    delivery = await _enqueue(dispatcher, user, key="no-device")
    assert delivery.suppression_reason == "no_device_token"

    await PushDeviceService(db_session).register(user, "fresh-phone-token-0001")
    revived = await dispatcher.reschedule_suppressed(user, now=NOW)

    assert revived == 1
    assert delivery.status == "scheduled"


@pytest.mark.asyncio
async def test_opening_a_push_marks_its_delivery(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = {"Authorization": "Bearer mock_token_push_opened"}
    me = await client.get("/api/v1/users/me", headers=headers)
    user = await db_session.get(User, me.json()["id"])
    await PushDeviceService(db_session).register(user, "opened-phone-token-001")
    dispatcher = PushDispatcher(db_session, gateway=RecordingGateway())
    delivery = await _enqueue(dispatcher, user, key="opened")
    await db_session.commit()
    await dispatcher.deliver(delivery.id, now=NOW)

    response = await client.post(
        "/api/v1/insights/analytics",
        json={
            "event_id": f"open-{delivery.id}-{uuid4().hex[:8]}",
            "event_name": "notification_opened",
            "source": "push",
            "metadata": {"delivery_id": delivery.id, "notification_type": "test_reminder"},
        },
        headers=headers,
    )
    stored = await db_session.get(NotificationDelivery, delivery.id)
    await db_session.refresh(stored)

    assert response.status_code == 204
    assert stored.opened_at is not None
