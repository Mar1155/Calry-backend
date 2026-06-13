import datetime as dt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository handling all user database queries."""

    def __init__(self, db: AsyncSession):
        super().__init__(User, db)

    async def get_by_firebase_uid(self, firebase_uid: str) -> User | None:
        """Looks up a user by their Firebase Unique Identifier."""
        stmt = select(User).where(User.firebase_uid == firebase_uid)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Looks up a user by their email address."""
        stmt = select(User).where(User.email == email)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_user_premium_status(
        self,
        user: User,
        is_premium: bool,
        premium_entitlement: str | None,
        premium_expires_at: dt.datetime | None,
        revenuecat_app_user_id: str | None,
    ) -> User:
        """Updates the subscription metadata for the user."""
        user.is_premium = is_premium
        user.premium_entitlement = premium_entitlement
        user.premium_expires_at = premium_expires_at
        user.revenuecat_app_user_id = revenuecat_app_user_id
        
        self.db.add(user)
        await self.db.flush()
        return user
