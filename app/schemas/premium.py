import datetime as dt
from pydantic import BaseModel


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
