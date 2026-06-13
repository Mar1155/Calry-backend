import pytest
import datetime as dt
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.meal import Meal
from app.ai.services.correction_context_service import AICorrectionContextService


@pytest.mark.asyncio
async def test_correction_context_service_summary(db_session: AsyncSession):
    """Tests that AICorrectionContextService correctly aggregates and formats correction history."""
    # 1. Create a dummy user
    user = User(
        firebase_uid="test_user_uid_123",
        email="test_user@calry.app",
        name="John Doe",
        daily_calorie_goal=2000,
        goal_type="maintain"
    )
    db_session.add(user)
    await db_session.flush()
    user_id = user.id

    # 2. Add some meals (some corrected, some exact, some unconfirmed)
    m1 = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Chicken and rice",
        meal_name="Chicken and Rice",
        estimated_calories=500,
        confirmed_calories=450,  # corrected: -50 kcal (-10.0%)
        correction_delta=-50,
        correction_percent=-10.0,
        ai_confidence="high"
    )
    m2 = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Salad",
        meal_name="Green Salad",
        estimated_calories=200,
        confirmed_calories=200,  # exact confirmation: 0 kcal (0.0%)
        correction_delta=0,
        correction_percent=0.0,
        ai_confidence="high"
    )
    m3 = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Pizza slice",
        meal_name="Pepperoni Pizza",
        estimated_calories=300,
        confirmed_calories=360,  # corrected: +60 kcal (+20.0%)
        correction_delta=60,
        correction_percent=20.0,
        ai_confidence="medium"
    )
    m4 = Meal(
        user_id=user_id,
        source_type="text",
        original_input="Unconfirmed meal",
        meal_name="Late Snack",
        estimated_calories=150,
        confirmed_calories=None,  # unconfirmed, should be skipped
        ai_confidence="low"
    )

    db_session.add_all([m1, m2, m3, m4])
    await db_session.flush()

    # 3. Query the service
    service = AICorrectionContextService(db_session)
    summary = await service.get_user_correction_summary(user_id)
    avg_pct = await service.get_average_correction_percent(user_id)

    # 4. Assertions
    # 3 confirmed meals:
    # m1: -10%
    # m2: 0%
    # m3: +20%
    # average pct: (-10 + 0 + 20) / 3 = +3.33%
    assert avg_pct is not None
    assert round(avg_pct, 2) == pytest.approx(3.33, abs=0.01)

    assert summary is not None
    # Verify summary structure contains correction stats and recent meals
    assert "calibrated/confirmed 3 meals recently" in summary
    assert "average change of +3.3%" in summary
    assert "Meal 'Chicken and Rice'" in summary
    assert "Meal 'Green Salad'" in summary
    assert "Meal 'Pepperoni Pizza'" in summary
    assert "Late Snack" not in summary  # skipped because unconfirmed
