from pydantic import BaseModel, ConfigDict


class AppVersionResponse(BaseModel):
    min_supported_app_version: str

    model_config = ConfigDict(from_attributes=True)
