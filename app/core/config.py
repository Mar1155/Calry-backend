from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENVIRONMENT: Literal["development", "testing", "production"] = "development"
    LOG_LEVEL: str = "info"
    PORT: int = 8000

    # Database URLs
    # Must use postgresql+asyncpg:// for SQLAlchemy async connections
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/calry"

    # Firebase configuration
    FIREBASE_CREDENTIALS_PATH: str | None = None
    FIREBASE_PROJECT_ID: str = "calry-62362"

    # AI API keys
    OPENROUTER_API_KEY: str | None = None
    DEFAULT_AI_PROVIDER: str = "openrouter"
    AI_PROVIDER: str = "openrouter"
    OPENROUTER_TEXT_MODEL: str = "google/gemini-2.5-flash"
    OPENROUTER_IMAGE_MODEL: str = "google/gemini-2.5-flash"
    OPENROUTER_AUDIO_MODEL: str = "google/gemini-2.5-flash"
    AI_REQUEST_TIMEOUT_SECONDS: float = 30.0
    AI_MAX_RETRIES: int = 1

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "testing"


settings = Settings()
