import math


class CalorieTargetService:
    """Service to estimate resting metabolic rate (BMR), maintenance calories,

    and target daily calorie goal based on the Mifflin-St Jeor equation.
    """

    @staticmethod
    def calculate_bmr(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
        """Estimates Basal Metabolic Rate (BMR) using the Mifflin-St Jeor equation.

        Male BMR = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
        Female BMR = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
        """
        if sex.lower() == "male":
            return 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age + 5.0
        elif sex.lower() == "female":
            return 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age - 161.0
        else:
            # Fallback/Default to Female average subtraction or a middle point if unspecified
            return 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age - 78.0

    ACTIVITY_FACTORS = {"low": 1.30, "light": 1.40, "moderate": 1.50, "high": 1.60}
    PACE_ADJUSTMENTS = {
        "lose": {"gradual": -250, "balanced": -400, "stronger": -500},
        "gain": {"gradual": 150, "balanced": 250, "stronger": 350},
        "maintain": {"balanced": 0},
    }

    @staticmethod
    def calculate_maintenance_calories(bmr: float, activity_level: str = "light") -> float:
        """Calculates maintenance using conservative server-owned activity mapping."""
        return bmr * CalorieTargetService.ACTIVITY_FACTORS[activity_level]

    @staticmethod
    def calculate_daily_target(maintenance_calories: float, goal_type: str, target_pace: str = "balanced") -> int:
        """Calculates final calorie target based on goal and rounds to the nearest 50 kcal.

        Lose weight: -400 kcal
        Gain weight: +300 kcal
        Maintain: +0 kcal
        """
        target = maintenance_calories + CalorieTargetService.PACE_ADJUSTMENTS[goal_type].get(target_pace, 0)

        # Round to nearest 50 kcal
        return math.floor(target / 50.0 + 0.5) * 50

    @staticmethod
    def requires_confirmation(selected: int, sex: str | None, suggested: int | None = None) -> bool:
        """Existing product guardrail, shared by onboarding and profile edits.

        These thresholds are not a clinical assessment of a person's needs.
        """
        minimum = 1500 if sex != "female" else 1200
        return selected < minimum or (suggested is not None and selected < suggested - 500)
