import datetime as dt

from pydantic import BaseModel, Field


class PremiumSyncRequest(BaseModel):
    is_premium: bool
    entitlement: str | None = None
    expires_at: dt.datetime | None = None
    revenuecat_app_user_id: str


class PremiumStatusResponse(BaseModel):
    is_premium: bool
    entitlement: str | None = None
    expires_at: dt.datetime | None = None
    source: str
    store: str | None = None
    product_id: str | None = None
    last_verified_at: dt.datetime | None = None


class PromoCodeRedeemRequest(BaseModel):
    code: str = Field(min_length=4, max_length=128)


class PromoCodeRedeemResponse(PremiumStatusResponse):
    redeemed: bool
    message: str


class AiQuotaResponse(BaseModel):
    """Today's free AI analyses (photo and voice). ``unlimited`` for Pro."""

    unlimited: bool
    limit: int | None = None
    used: int
    remaining: int | None = None
    reset_at: dt.datetime
