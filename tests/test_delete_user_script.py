from sqlalchemy import select

from app.models.daily_summary import DailySummary
from app.models.inference import AIInferenceLog
from app.models.meal import Meal, MealItem
from app.models.revenuecat_event import RevenueCatEvent, RevenueCatSubscriberSnapshot
from app.models.user import User
from scripts.delete_user import delete_database_user, deletion_target, related_data_counts


async def test_delete_database_user_removes_personal_data(db_session):
    user = User(firebase_uid="delete-me-uid", email="Delete.Me@Example.com")
    db_session.add(user)
    await db_session.flush()

    meal = Meal(user_id=user.id, source_type="text", original_input="private meal", estimated_calories=100)
    db_session.add(meal)
    await db_session.flush()
    db_session.add(MealItem(meal_id=meal.id, name="Private food", weight_grams=100, calories_per_100g=100))
    db_session.add(DailySummary(user_id=user.id, date=user.created_at.date(), remaining_calories=1900))
    db_session.add(
        AIInferenceLog(
            user_id=user.id,
            provider="test",
            model_name="test",
            prompt_version="v1",
            input_type="text",
            raw_input="private input",
            latency_ms=1,
        )
    )
    db_session.add(
        RevenueCatEvent(
            event_id="delete-user-event",
            event_type="TEST",
            app_user_id=user.firebase_uid,
            payload={"app_user_id": user.firebase_uid},
        )
    )
    db_session.add(
        RevenueCatSubscriberSnapshot(
            app_user_id=user.firebase_uid,
            user_id=user.id,
            entitlement_active=False,
            snapshot={"private": True},
        )
    )
    await db_session.commit()

    target = deletion_target(user)
    counts = await related_data_counts(db_session, target)
    assert counts["meals"] == 1
    assert counts["meal_items"] == 1
    assert counts["ai_inference_logs"] == 1
    assert counts["revenuecat_events"] == 1

    await delete_database_user(db_session, target)

    assert await db_session.get(User, target.id) is None
    assert (await db_session.execute(select(Meal).where(Meal.user_id == target.id))).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(AIInferenceLog).where(AIInferenceLog.user_id == target.id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(RevenueCatEvent).where(RevenueCatEvent.app_user_id == target.firebase_uid))
    ).scalar_one_or_none() is None


async def test_user_email_lookup_is_case_insensitive(db_session):
    from scripts.delete_user import find_user_by_email

    user = User(firebase_uid="case-delete-uid", email="Case.Delete@Example.com")
    db_session.add(user)
    await db_session.commit()

    found = await find_user_by_email(db_session, "  CASE.DELETE@example.COM ")
    assert found is not None
    assert found.id == user.id
