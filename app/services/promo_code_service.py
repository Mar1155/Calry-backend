import datetime as dt
import hashlib
import hmac
import logging
import re
import unicodedata

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.promo_code import PromoCode, PromoCodeAttempt, PromoCodeRedemption
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.premium import PromoCodeRedeemResponse
from app.services.revenuecat_service import RevenueCatAPIError, RevenueCatClient, derive_entitlement_state

logger = logging.getLogger("app.services.promo_code")


def normalize_promo_code(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).upper()
    return re.sub(r"[^A-Z0-9]", "", normalized)


def promo_code_digest(value: str, pepper: str) -> str:
    normalized = normalize_promo_code(value)
    return hmac.new(pepper.encode(), normalized.encode(), hashlib.sha256).hexdigest()


def promo_code_hint(value: str) -> str:
    normalized = normalize_promo_code(value)
    return f"{normalized[:4]}…{normalized[-4:]}" if len(normalized) > 8 else f"{normalized[:2]}…"


class PromoCodeService:
    def __init__(self, db: AsyncSession, revenuecat_client: RevenueCatClient | None = None):
        self.db = db
        self.revenuecat_client = revenuecat_client or RevenueCatClient()
        self.user_repo = UserRepository(db)

    async def redeem_free_access(self, user: User, raw_code: str) -> PromoCodeRedeemResponse:
        user_id = user.id
        firebase_uid = user.firebase_uid
        pepper = settings.PROMO_CODE_PEPPER
        if not settings.PROMO_CODE_REDEMPTION_ENABLED or not pepper or not self.revenuecat_client.is_configured:
            logger.error("promo_code_unavailable reason=server_not_configured")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Code redemption is temporarily unavailable.",
            )

        normalized = normalize_promo_code(raw_code)
        digest = promo_code_digest(raw_code, pepper)
        await self._enforce_attempt_limit(user_id)
        if len(normalized) < 8 or len(normalized) > 64:
            await self._record_attempt(user_id, digest, False)
            raise self._invalid_code()

        now = dt.datetime.now(dt.UTC)

        stmt = select(PromoCode).where(PromoCode.code_digest == digest).with_for_update()
        promo = (await self.db.execute(stmt)).scalar_one_or_none()
        if promo is None or not self._is_available(promo, now):
            await self._record_attempt(user_id, digest, False)
            raise self._invalid_code()

        existing_stmt = select(PromoCodeRedemption).where(
            PromoCodeRedemption.promo_code_id == promo.id,
            PromoCodeRedemption.user_id == user_id,
        )
        existing = (await self.db.execute(existing_stmt)).scalar_one_or_none()
        was_already_granted = existing is not None and existing.status == "granted"
        if was_already_granted and user.is_premium:
            await self._record_attempt(user_id, digest, True)
            return self._response(user, redeemed=False, message="This code is already active on your account.")

        if (
            not was_already_granted
            and promo.max_redemptions is not None
            and promo.redemption_count >= promo.max_redemptions
        ):
            await self._record_attempt(user_id, digest, False)
            raise self._invalid_code()

        if existing is None:
            existing = PromoCodeRedemption(
                promo_code_id=promo.id,
                user_id=user_id,
                status="pending",
                revenuecat_app_user_id=firebase_uid,
            )
            self.db.add(existing)
        else:
            existing.status = "pending"
            existing.failure_reason = None

        if not was_already_granted:
            promo.redemption_count += 1
        await self.db.flush()

        try:
            customer_info = await self.revenuecat_client.grant_promotional_entitlement(
                firebase_uid,
                settings.REVENUECAT_ENTITLEMENT_ID,
                duration=promo.grant_duration,
            )
        except RevenueCatAPIError as exc:
            await self.db.rollback()
            await self._record_attempt(user_id, digest, False)
            logger.error("promo_code_grant_failed user_id=%s error=%s", user_id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The code could not be activated right now. Please try again.",
            ) from exc

        entitlement = derive_entitlement_state(customer_info, settings.REVENUECAT_ENTITLEMENT_ID)
        if not entitlement.is_active:
            await self.db.rollback()
            await self._record_attempt(user_id, digest, False)
            logger.error("promo_code_grant_inactive user_id=%s", user_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The code could not be activated right now. Please try again.",
            )

        existing.status = "granted"
        existing.redeemed_at = now
        existing.grant_expires_at = entitlement.expires_at
        await self.user_repo.update_user_premium_status(
            user=user,
            is_premium=True,
            premium_entitlement=settings.REVENUECAT_ENTITLEMENT_ID,
            premium_expires_at=entitlement.expires_at,
            revenuecat_app_user_id=firebase_uid,
            premium_store=entitlement.store or "promotional",
            premium_product_id=entitlement.product_id,
        )
        self.db.add(PromoCodeAttempt(user_id=user_id, code_digest=digest, succeeded=True))
        await self.db.commit()

        logger.info("promo_code_redeemed user_id=%s promo_code_id=%s", user_id, promo.id)
        return self._response(user, redeemed=True, message="Calry Pro is now active on your account.")

    async def _enforce_attempt_limit(self, user_id: int) -> None:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=settings.PROMO_CODE_ATTEMPT_WINDOW_MINUTES)
        stmt = select(func.count(PromoCodeAttempt.id)).where(
            PromoCodeAttempt.user_id == user_id,
            PromoCodeAttempt.succeeded.is_(False),
            PromoCodeAttempt.attempted_at >= cutoff,
        )
        attempts = int((await self.db.execute(stmt)).scalar_one())
        if attempts >= settings.PROMO_CODE_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many code attempts. Please try again later.",
            )

    async def _record_attempt(self, user_id: int, digest: str, succeeded: bool) -> None:
        self.db.add(PromoCodeAttempt(user_id=user_id, code_digest=digest, succeeded=succeeded))
        await self.db.commit()

    @staticmethod
    def _is_available(promo: PromoCode, now: dt.datetime) -> bool:
        return (
            promo.is_active
            and promo.kind == "free_access"
            and (promo.valid_from is None or promo.valid_from <= now)
            and (promo.valid_until is None or promo.valid_until > now)
        )

    @staticmethod
    def _invalid_code() -> HTTPException:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Code is invalid or unavailable.")

    @staticmethod
    def _response(user: User, *, redeemed: bool, message: str) -> PromoCodeRedeemResponse:
        return PromoCodeRedeemResponse(
            redeemed=redeemed,
            message=message,
            is_premium=user.is_premium,
            entitlement=user.premium_entitlement,
            expires_at=user.premium_expires_at,
            source="promo_code" if user.is_premium else "none",
            store=user.premium_store,
            product_id=user.premium_product_id,
            last_verified_at=user.premium_last_verified_at,
        )
