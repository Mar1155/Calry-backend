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

    @staticmethod
    def calculate_maintenance_calories(bmr: float) -> float:
        """Calculates maintenance calories assuming a light-to-moderate activity multiplier of 1.4."""
        return bmr * 1.4

    @staticmethod
    def calculate_daily_target(maintenance_calories: float, goal_type: str) -> int:
        """Calculates final calorie target based on goal and rounds to the nearest 50 kcal.

        Lose weight: -400 kcal
        Gain weight: +300 kcal
        Maintain: +0 kcal
        """
        target = maintenance_calories
        if goal_type == "lose":
            target -= 400.0
        elif goal_type == "gain":
            target += 300.0

        # Round to nearest 50 kcal
        return int(round(target / 50.0)) * 50
