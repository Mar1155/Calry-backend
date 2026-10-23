from pydantic import BaseModel, ConfigDict, field_validator

from app.core.app_version import is_valid_version


class AppVersionResponse(BaseModel):
    """Version gate policy for one client. `min_supported_app_version` is the
    effective minimum for the requesting platform (platform override, else the
    global floor)."""

    min_supported_app_version: str
    store_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AppVersionPolicy(BaseModel):
    """Full policy as managed from the admin console."""

    min_supported_app_version: str
    min_supported_ios_version: str | None = None
    min_supported_android_version: str | None = None
    ios_store_url: str | None = None
    android_store_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AppVersionPolicyUpdate(BaseModel):
    min_supported_app_version: str
    min_supported_ios_version: str | None = None
    min_supported_android_version: str | None = None
    ios_store_url: str | None = None
    android_store_url: str | None = None

    @field_validator("min_supported_app_version")
    @classmethod
    def _required_version(cls, value: str) -> str:
        value = value.strip()
        if not is_valid_version(value):
            raise ValueError("Must be a dotted numeric version such as 1.2.3")
        return value

    @field_validator("min_supported_ios_version", "min_supported_android_version")
    @classmethod
    def _optional_version(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not is_valid_version(value):
            raise ValueError("Must be a dotted numeric version such as 1.2.3")
        return value

    @field_validator("ios_store_url", "android_store_url")
    @classmethod
    def _store_url(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not value.startswith("https://") or len(value) > 512:
            raise ValueError("Must be an https:// URL")
        return value
