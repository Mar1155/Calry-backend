import datetime as dt
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.models.daily_summary import DailySummary
from app.repositories.burned_calories import BurnedCaloriesRepository
from app.repositories.daily_summary import DailySummaryRepository
from app.repositories.meal import MealRepository
from app.repositories.user import UserRepository

logger = logging.getLogger("app.services.summary")


class SummaryService:
    """Business service responsible for computing and synchronizing daily summaries.

    It aggregates caloric logs (meals and burned calories) dynamically and maps them
    to the user's target calorie allowance.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.summary_repo = DailySummaryRepository(db)
        self.meal_repo = MealRepository(db)
        self.burned_repo = BurnedCaloriesRepository(db)
        self.user_repo = UserRepository(db)

    async def sync_daily_summary(self, user_id: int, date_val: dt.date) -> DailySummary:
        """Aggregates all meal logs and energy expenditure logs for a user on a given day.

        Calculates the updated daily caloric balance, commits the changes to the database,
        and returns the consolidated DailySummary.
        """
        logger.info(f"Synchronizing daily summary for user_id={user_id} on date={date_val}")

        # 1. Load user settings to acquire calorie goal baseline
        user = await self.user_repo.get(user_id)
        if not user:
            raise NotFoundException(
                message=f"User with ID {user_id} could not be resolved.",
                error_code="USER_NOT_FOUND",
            )

        # 2. Gather meals consumed on this date
        meals = await self.meal_repo.get_user_meals_on_date(user_id, date_val)
        consumed_calories = 0
        for meal in meals:
            # Prefer manually confirmed calorie estimates if provided
            consumed_calories += (
                meal.confirmed_calories if meal.confirmed_calories is not None else meal.estimated_calories
            )

        # 3. Gather calories burned on this date
        burned_logs = await self.burned_repo.get_user_burned_on_date(user_id, date_val)
        burned_calories = sum(log.calories for log in burned_logs)

        # 4. Calculate final caloric balance
        # Standard balance definition: Goal - Consumed + Burned = Remaining Caloric Allowance
        remaining_calories = user.daily_calorie_goal - consumed_calories + burned_calories

        # 5. Insert or Update target summary
        summary = await self.summary_repo.get_by_date(user_id, date_val)
        if summary:
            summary.consumed_calories = consumed_calories
            summary.burned_calories = burned_calories
            summary.remaining_calories = remaining_calories
            logger.debug(f"Updated existing summary: ID={summary.id}")
        else:
            summary = DailySummary(
                user_id=user_id,
                date=date_val,
                consumed_calories=consumed_calories,
                burned_calories=burned_calories,
                remaining_calories=remaining_calories,
            )
            await self.summary_repo.create(summary)
            logger.debug("Created new DailySummary entry.")

        await self.db.flush()
        return summary
