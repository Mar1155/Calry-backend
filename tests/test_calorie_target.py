import pytest
from app.services.calorie_target_service import CalorieTargetService
from httpx import AsyncClient

def test_calorie_target_service_math():
    # Male test case: weight=75, height=180, age=28, sex=male
    # BMR = 10*75 + 6.25*180 - 5*28 + 5 = 750 + 1125 - 140 + 5 = 1740
    bmr_m = CalorieTargetService.calculate_bmr(75, 180, 28, "male")
    assert bmr_m == 1740.0
    
    # Female test case: weight=60, height=165, age=30, sex=female
    # BMR = 10*60 + 6.25*165 - 5*30 - 161 = 600 + 1031.25 - 150 - 161 = 1320.25
    bmr_f = CalorieTargetService.calculate_bmr(60, 165, 30, "female")
    assert bmr_f == 1320.25

    # Maintenance
    maint_m = CalorieTargetService.calculate_maintenance_calories(bmr_m)
    assert maint_m == 1740.0 * 1.4 # 2436.0

    # Daily target rounding to nearest 50
    # Maintain: 2436 -> 2450
    assert CalorieTargetService.calculate_daily_target(maint_m, "maintain") == 2450
    # Lose: 2436 - 400 = 2036 -> 2050
    assert CalorieTargetService.calculate_daily_target(maint_m, "lose") == 2050
    # Gain: 2436 + 300 = 2736 -> 2750
    assert CalorieTargetService.calculate_daily_target(maint_m, "gain") == 2750


@pytest.mark.asyncio
async def test_update_profile_estimates_goal(client: AsyncClient):
    headers = {"Authorization": "Bearer mock_token_user_abc_123"}
    # Warm up registration
    await client.get("/api/v1/users/me", headers=headers)

    payload = {
        "sex": "male",
        "age": 28,
        "height_cm": 180.0,
        "weight_kg": 75.0,
        "goal_type": "maintain"
    }
    response = await client.patch("/api/v1/users/me", json=payload, headers=headers)
    assert response.status_code == 200
    data = response.json()
    # Verify fields were saved
    assert data["sex"] == "male"
    assert data["age"] == 28
    assert data["height_cm"] == 180.0
    assert data["weight_kg"] == 75.0
    assert data["goal_type"] == "maintain"
    # Verify estimated calorie goal got updated to 2450
    assert data["daily_calorie_goal"] == 2450
