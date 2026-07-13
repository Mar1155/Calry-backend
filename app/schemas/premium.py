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
