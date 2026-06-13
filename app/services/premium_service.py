from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.premium import PremiumSyncRequest, PremiumStatusResponse


class PremiumService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)

    async def sync_premium(self, user: User, sync_req: PremiumSyncRequest) -> User:
        """Updates and persists the premium sync data for the active user."""
        updated_user = await self.user_repo.update_user_premium_status(
            user=user,
            is_premium=sync_req.is_premium,
            premium_entitlement=sync_req.entitlement,
            premium_expires_at=sync_req.expires_at,
            revenuecat_app_user_id=sync_req.revenuecat_app_user_id,
        )
        await self.db.commit()
        return updated_user

    async def get_premium_status(self, user: User) -> PremiumStatusResponse:
        """Returns verified premium status info from the local database context."""
        return PremiumStatusResponse(
            is_premium=user.is_premium,
            entitlement=user.premium_entitlement,
            expires_at=user.premium_expires_at,
            source="backend",
        )
