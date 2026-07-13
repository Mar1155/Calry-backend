import datetime as dt
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.premium import PremiumStatusResponse, PremiumSyncRequest
from app.services.revenuecat_service import RevenueCatClient, derive_entitlement_state

logger = logging.getLogger("app.services.premium")


class PremiumService:
    def __init__(self, db: AsyncSession, revenuecat_client: RevenueCatClient | None = None):
        self.db = db
        self.user_repo = UserRepository(db)
        self.revenuecat_client = revenuecat_client or RevenueCatClient()

    async def sync_premium(self, user: User, sync_req: PremiumSyncRequest) -> User:
        """Updates and persists the premium sync data for the active user."""
        if user.is_premium != sync_req.is_premium:
            logger.info(
                "premium status change user_id=%s %s->%s entitlement=%s source=client_sync",
                user.id,
                user.is_premium,
                sync_req.is_premium,
                sync_req.entitlement,
            )
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
        if settings.PREMIUM_BYPASS:
            return PremiumStatusResponse(
                is_premium=True,
                entitlement=settings.REVENUECAT_ENTITLEMENT_ID,
                expires_at=None,
                source="bypass",
                store="bypass",
            )
        now = dt.datetime.now(dt.UTC)
        expires_at = user.premium_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            # SQLite drops timezone metadata in tests; PostgreSQL preserves it.
            # Normalize defensively so entitlement expiry is consistent in both.
            expires_at = expires_at.replace(tzinfo=dt.UTC)
        if user.is_premium and expires_at and expires_at <= now:
            await self.user_repo.update_user_premium_status(
                user=user,
                is_premium=False,
                premium_entitlement=None,
                premium_expires_at=user.premium_expires_at,
                revenuecat_app_user_id=user.revenuecat_app_user_id,
                premium_store=user.premium_store,
                premium_product_id=user.premium_product_id,
            )
            await self.db.commit()

        source = "none"
        if user.is_premium:
            source = "promo_code" if user.premium_store == "promotional" else "revenuecat"
        return PremiumStatusResponse(
            is_premium=user.is_premium,
            entitlement=user.premium_entitlement,
            expires_at=user.premium_expires_at,
            source=source,
            store=user.premium_store,
            product_id=user.premium_product_id,
            last_verified_at=user.premium_last_verified_at,
        )

    async def refresh_from_revenuecat(self, user: User) -> PremiumStatusResponse:
        """Verifies current access server-side; never trusts a client boolean."""
        subscriber = await self.revenuecat_client.get_subscriber(user.firebase_uid)
        state = derive_entitlement_state(subscriber, settings.REVENUECAT_ENTITLEMENT_ID)
        await self.user_repo.update_user_premium_status(
            user=user,
            is_premium=state.is_active,
            premium_entitlement=settings.REVENUECAT_ENTITLEMENT_ID if state.is_active else None,
            premium_expires_at=state.expires_at,
            revenuecat_app_user_id=user.firebase_uid,
            premium_store=state.store,
            premium_product_id=state.product_id,
        )
        await self.db.commit()
        return await self.get_premium_status(user)
